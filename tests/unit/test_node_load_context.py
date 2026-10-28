import logging
from datetime import date
from typing import Any

import pytest

from hackathon_sniper.models import Lifecycle, Profile
from hackathon_sniper.nodes.load_context import load_context
from hackathon_sniper.notion.client import NotionError
from hackathon_sniper.notion.mapping import event_to_properties, profile_to_properties
from tests.factories import event, make_deps
from tests.fakes import FakeNotion


def _participation(hackathon_id: str, project: str = "Bot") -> dict[str, Any]:
    return {
        "Project": {"title": [{"plain_text": project}]},
        "Hackathon": {"relation": [{"id": hackathon_id}]},
    }


def test_loads_profile_existing_rows_and_history() -> None:
    notion = FakeNotion()
    notion.add_page(
        None,
        profile_to_properties(Profile(interests=["AI"], min_prize_usd=500)),
        page_id="profile",
        text="Avoid crypto.",
    )
    hid = notion.add_page(
        "hackathons", event_to_properties(event(url="https://a.devpost.com", name="A"))
    )
    notion.add_page("participations", _participation(hid))

    out = load_context({}, make_deps(notion=notion))

    assert out["profile"].interests == ["AI"]
    assert out["profile"].notes == "Avoid crypto."
    assert out["profile"].min_prize_usd == 500
    assert list(out["existing"]) == ["https://a.devpost.com"]
    assert out["existing"]["https://a.devpost.com"].notion_page_id == hid
    assert out["history"][0].hackathon == "A"


def test_empty_databases_give_empty_context() -> None:
    out = load_context({}, make_deps())

    assert out["existing"] == {}
    assert out["history"] == []


def test_unusable_rows_are_skipped() -> None:
    notion = FakeNotion()
    notion.add_page("hackathons", {"Name": {"title": [{"plain_text": "No URL"}]}})
    good = notion.add_page("hackathons", event_to_properties(event(url="https://b.devpost.com")))

    out = load_context({}, make_deps(notion=notion))

    assert [e.notion_page_id for e in out["existing"].values()] == [good]


def test_notion_error_from_query_all_propagates() -> None:
    """Syncing without knowing existing rows would duplicate them, so the run must abort."""
    notion = FakeNotion()
    deps = make_deps(notion=notion, notion_hackathons_db_id="missing-db")
    notion.databases.discard("missing-db")

    with pytest.raises(NotionError):
        load_context({}, deps)


def test_notion_error_from_participations_propagates() -> None:
    notion = FakeNotion()
    deps = make_deps(notion=notion)
    notion.databases.discard("participations")

    with pytest.raises(NotionError):
        load_context({}, deps)


def test_missing_profile_page_propagates() -> None:
    notion = FakeNotion()
    deps = make_deps(notion=notion)
    del notion.pages["profile"]

    with pytest.raises(NotionError):
        load_context({}, deps)


def test_duplicate_rows_prefer_active_over_expired(caplog: pytest.LogCaptureFixture) -> None:
    notion = FakeNotion()
    url = "https://a.devpost.com"
    notion.add_page(
        "hackathons",
        event_to_properties(
            event(url=url, lifecycle=Lifecycle.EXPIRED, last_seen=date(2026, 10, 25))
        ),
        page_id="expired",
    )
    notion.add_page(
        "hackathons",
        event_to_properties(event(url=url, last_seen=date(2026, 1, 1))),
        page_id="active",
    )

    with caplog.at_level(logging.WARNING):
        out = load_context({}, make_deps(notion=notion))

    assert out["existing"][url].notion_page_id == "active"
    assert "Duplicate" in caplog.text


def test_duplicate_rows_prefer_most_recently_seen_or_verified() -> None:
    notion = FakeNotion()
    url = "https://a.devpost.com"
    notion.add_page(
        "hackathons",
        event_to_properties(event(url=url, last_seen=date(2026, 10, 1))),
        page_id="old",
    )
    notion.add_page(
        "hackathons",
        event_to_properties(event(url=url, verified_at=date(2026, 10, 20))),
        page_id="newer",
    )
    notion.add_page("hackathons", event_to_properties(event(url=url)), page_id="undated")

    out = load_context({}, make_deps(notion=notion))

    assert out["existing"][url].notion_page_id == "newer"


def test_duplicate_rows_tie_keeps_first_seen() -> None:
    notion = FakeNotion()
    url = "https://a.devpost.com"
    for pid in ("first", "second"):
        notion.add_page("hackathons", event_to_properties(event(url=url)), page_id=pid)

    out = load_context({}, make_deps(notion=notion))

    assert out["existing"][url].notion_page_id == "first"


def test_participation_pointing_at_duplicate_row_still_resolves() -> None:
    notion = FakeNotion()
    url = "https://a.devpost.com"
    notion.add_page("hackathons", event_to_properties(event(url=url, name="Kept")), page_id="kept")
    notion.add_page(
        "hackathons",
        event_to_properties(event(url=url, name="Dropped", lifecycle=Lifecycle.EXPIRED)),
        page_id="dropped",
    )
    notion.add_page("participations", _participation("dropped"))
    notion.add_page("participations", _participation("unknown-id", project="Orphan"))

    out = load_context({}, make_deps(notion=notion))

    assert out["existing"][url].notion_page_id == "kept"
    assert [h.hackathon for h in out["history"]] == ["Dropped", None]
