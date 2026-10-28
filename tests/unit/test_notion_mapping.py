import logging
import math
from datetime import date
from typing import Any

import pytest

from hackathon_sniper.models import (
    Format,
    HackathonEvent,
    Lifecycle,
    Origin,
    Profile,
    Solo,
    TeamPreference,
    Verification,
)
from hackathon_sniper.notion.mapping import (
    _select,
    event_to_properties,
    page_to_event,
    page_to_participation,
    page_to_profile,
    profile_to_properties,
)


def test_event_round_trip() -> None:
    ev = HackathonEvent(
        canonical_url="https://a.devpost.com",
        name="A",
        sources=["devpost", "web"],
        registration_deadline=date(2026, 11, 1),
        start_date=date(2026, 10, 1),
        end_date=date(2026, 11, 1),
        prize_usd=5000,
        format=Format.ONLINE,
        solo_allowed=Solo.YES,
        themes=["AI"],
        verification=Verification.VERIFIED,
        verified_at=date(2026, 10, 26),
        last_seen=date(2026, 10, 26),
        fit_score=80,
        fit_reason="good",
        notion_page_id="p1",
        origin=Origin.PAGE,
    )
    assert page_to_event({"id": "p1", "properties": event_to_properties(ev)}) == ev


def test_page_without_url_is_skipped() -> None:
    page = {"id": "x", "properties": {"Name": {"title": [{"plain_text": "No URL"}]}}}
    assert page_to_event(page) is None


def test_unknown_select_option_falls_back() -> None:
    props = event_to_properties(HackathonEvent(canonical_url="https://a.dev", name="A"))
    props["Format"] = {"select": {"name": "Metaverse"}}
    ev = page_to_event({"id": "p", "properties": props})
    assert ev is not None and ev.format == Format.UNKNOWN


def test_profile_round_trip_with_body_notes() -> None:
    profile = Profile(
        skills=["Python"],
        interests=["AI agents"],
        formats=[Format.ONLINE],
        team=TeamPreference.SOLO_ONLY,
        min_prize_usd=1000,
        location="Remote",
    )
    page = {"id": "pr", "properties": profile_to_properties(profile)}
    expected = profile.model_copy(update={"notes": "Avoid crypto."})
    assert page_to_profile(page, "Avoid crypto.\n") == expected


def test_participation_resolves_relation_names() -> None:
    page = {
        "id": "x",
        "properties": {
            "Project": {"title": [{"plain_text": "SniperBot"}]},
            "Hackathon": {"relation": [{"id": "h1"}]},
            "Role": {"select": {"name": "Backend"}},
            "Tech Stack": {"multi_select": [{"name": "Python"}]},
            "Outcome": {"select": {"name": "Winner"}},
            "Notes": {"rich_text": [{"plain_text": "Loved it"}]},
        },
    }
    p = page_to_participation(page, {"h1": "Big Hack"})
    assert (p.hackathon, p.project, p.role, p.tech_stack, p.outcome, p.notes) == (
        "Big Hack",
        "SniperBot",
        "Backend",
        ["Python"],
        "Winner",
        "Loved it",
    )


# ---- robustness: readers must survive hand-edited Notion rows -------------------------------


def _page(**props: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "Name": {"title": [{"plain_text": "Hack"}]},
        "URL": {"url": "https://hack.dev"},
    }
    base.update(props)
    return {"id": "pg-1", "properties": base}


@pytest.mark.parametrize(
    "url_prop",
    [
        {"url": "not a url"},  # host-less: canonicalize raises ValidationError
        {"url": "mailto:"},
        {"url": None},
        {"url": 123},
        {"rich_text": []},
        None,
    ],
)
def test_page_with_unusable_url_is_skipped_with_warning(
    url_prop: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="hackathon_sniper.notion.mapping"):
        assert page_to_event(_page(URL=url_prop)) is None
    assert any("pg-1" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.parametrize(
    "page",
    [
        {"id": "junk-1", "properties": "garbage"},
        {"id": "junk-2"},
        {"id": "junk-3", "properties": {"Name": "Hack", "URL": "https://hack.dev"}},
        {"id": "junk-4", "properties": {"Name": {"title": "Hack"}, "URL": {"url": ["x"]}}},
    ],
)
def test_junk_pages_are_skipped(page: dict[str, Any], caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="hackathon_sniper.notion.mapping"):
        assert page_to_event(page) is None
    assert page["id"] in caplog.text


def test_unexpected_error_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(_: Any) -> Any:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("hackathon_sniper.notion.mapping._read_multi", boom)
    with caplog.at_level(logging.WARNING, logger="hackathon_sniper.notion.mapping"):
        assert page_to_event(_page()) is None
    assert "pg-1" in caplog.text and "kaboom" in caplog.text


def test_minimal_row_uses_defaults() -> None:
    ev = page_to_event(_page())
    assert ev == HackathonEvent(
        canonical_url="https://hack.dev", name="Hack", notion_page_id="pg-1"
    )


def test_null_and_empty_properties_read_as_defaults() -> None:
    ev = page_to_event(
        _page(
            **{
                "Sources": {"multi_select": None},
                "Registration Deadline": {"date": None},
                "Event Dates": {"date": {"start": None, "end": None}},
                "Prize USD": {"number": None},
                "Format": {"select": None},
                "Location": {"rich_text": None},
                "Solo": {"select": {"name": None}},
                "Themes": {"multi_select": []},
                "Description": {"rich_text": [None, {"plain_text": None}]},
                "Fit Score": None,
                "Verification": {},
                "Lifecycle": {"select": {}},
            }
        )
    )
    assert ev is not None
    assert (ev.sources, ev.registration_deadline, ev.start_date, ev.end_date) == (
        [],
        None,
        None,
        None,
    )
    assert (ev.prize_usd, ev.format, ev.location, ev.solo_allowed) == (
        None,
        Format.UNKNOWN,
        None,
        Solo.UNKNOWN,
    )
    assert (ev.description, ev.fit_score, ev.verification, ev.lifecycle) == (
        "",
        None,
        Verification.UNVERIFIED,
        Lifecycle.ACTIVE,
    )


def test_iso_datetimes_keep_their_calendar_date() -> None:
    ev = page_to_event(
        _page(
            **{
                "Registration Deadline": {"date": {"start": "2026-11-01T09:00:00.000+08:00"}},
                "Event Dates": {
                    "date": {
                        "start": "2026-11-02T00:00:00Z",
                        "end": "2026-11-04T23:59:00.000-05:00",
                    }
                },
            }
        )
    )
    assert ev is not None
    assert (ev.registration_deadline, ev.start_date, ev.end_date) == (
        date(2026, 11, 1),
        date(2026, 11, 2),
        date(2026, 11, 4),
    )


def test_malformed_dates_degrade_to_none_but_keep_the_row() -> None:
    # Dropping the row would make sync treat a known event as new and duplicate it.
    ev = page_to_event(
        _page(
            **{
                "Registration Deadline": {"date": {"start": "next friday"}},
                "Event Dates": {"date": {"start": "2026-13-45", "end": 20261101}},
                "Verified At": {"date": "2026-11-01"},
                "Last Seen": {"date": {"start": ""}},
            }
        )
    )
    assert ev is not None
    assert (ev.registration_deadline, ev.start_date, ev.end_date) == (None, None, None)
    assert (ev.verified_at, ev.last_seen) == (None, None)


def test_floats_in_number_fields_are_rounded() -> None:
    ev = page_to_event(_page(**{"Prize USD": {"number": 1500.0}, "Fit Score": {"number": 72.6}}))
    assert ev is not None and (ev.prize_usd, ev.fit_score) == (1500, 73)


@pytest.mark.parametrize("junk", [math.nan, math.inf, -5, True, "12", [1], {"n": 1}])
def test_junk_numbers_read_as_none(junk: Any) -> None:
    ev = page_to_event(_page(**{"Prize USD": {"number": junk}, "Fit Score": {"number": junk}}))
    assert ev is not None and (ev.prize_usd, ev.fit_score) == (None, None)


def test_prize_zero_round_trips_as_zero() -> None:
    ev = HackathonEvent(canonical_url="https://a.dev", name="A", prize_usd=0)
    props = event_to_properties(ev)
    assert props["Prize USD"] == {"number": 0}
    back = page_to_event({"id": "p", "properties": props})
    assert back is not None and back.prize_usd == 0


def test_columns_whose_type_was_changed_still_read() -> None:
    ev = page_to_event(
        {
            "id": "pg-2",
            "properties": {
                "Name": {"rich_text": [{"plain_text": " Hack "}]},
                "URL": {"rich_text": [{"plain_text": "https://hack.dev/"}]},
                "Format": {"multi_select": [{"name": "Online"}, {"name": "Hybrid"}]},
                "Solo": {"rich_text": [{"plain_text": "yes"}]},
                "Verification": {"status": {"name": "Verified"}},
                "Themes": {"rich_text": [{"plain_text": "AI, Web3 ,, AI"}]},
                "Sources": {"select": {"name": "devpost"}},
                "Prize USD": {"rich_text": [{"plain_text": "$1,500"}]},
                "Fit Score": {"rich_text": [{"plain_text": "seventy"}]},
                "Location": {"select": {"name": "Taipei"}},
                "Registration Deadline": {"rich_text": [{"plain_text": "2026-11-01"}]},
                "Lifecycle": {"multi_select": [{"name": "Expired"}]},
            },
        }
    )
    assert ev is not None
    assert (ev.name, ev.canonical_url, ev.format, ev.solo_allowed) == (
        "Hack",
        "https://hack.dev",
        Format.ONLINE,
        Solo.YES,
    )
    assert (ev.verification, ev.origin, ev.themes, ev.sources) == (
        Verification.VERIFIED,
        Origin.PAGE,
        ["AI", "Web3"],
        ["devpost"],
    )
    assert (ev.prize_usd, ev.fit_score, ev.location) == (1500, None, "Taipei")
    assert (ev.registration_deadline, ev.lifecycle) == (date(2026, 11, 1), Lifecycle.EXPIRED)


def test_select_options_match_case_insensitively_and_unknowns_fall_back() -> None:
    ev = page_to_event(
        _page(
            Format={"select": {"name": "in-PERSON"}},
            Solo={"select": {"name": "Maybe"}},
            Lifecycle={"select": {"name": "Archived"}},
        )
    )
    assert ev is not None
    assert (ev.format, ev.solo_allowed, ev.lifecycle) == (
        Format.IN_PERSON,
        Solo.UNKNOWN,
        Lifecycle.ACTIVE,
    )


def test_profile_tolerates_odd_rows() -> None:
    page = {
        "id": "pr",
        "properties": {
            "Skills": {"rich_text": [{"plain_text": "Python, Rust"}]},
            "Interests": None,
            "Format": {
                "multi_select": [{"name": "online"}, {"name": "Metaverse"}, {"name": "Online"}]
            },
            "Team": {"select": {"name": "Whatever"}},
            "Min Prize": {"number": 999.6},
            "Location": {"rich_text": []},
        },
    }
    assert page_to_profile(page, "  ") == Profile(
        skills=["Python", "Rust"], formats=[Format.ONLINE], min_prize_usd=1000
    )
    assert page_to_profile({"id": "empty"}, "") == Profile()


def test_participation_tolerates_odd_rows() -> None:
    page = {
        "id": "x",
        "properties": {
            "Hackathon": {"relation": [None, {"id": "missing"}, {"id": "h2"}]},
            "Role": {"multi_select": [{"name": "Frontend"}]},
            "Tech Stack": {"select": {"name": "Go"}},
            "Outcome": {"select": None},
        },
    }
    p = page_to_participation(page, {"h2": "Second"})
    assert (p.hackathon, p.project, p.role, p.tech_stack, p.outcome, p.notes) == (
        "Second",
        "",
        "Frontend",
        ["Go"],
        None,
        "",
    )
    assert page_to_participation(
        {"id": "y", "properties": {"Hackathon": {"relation": None}}}, {}
    ) == (page_to_participation({"id": "z"}, {}))


# ---- writers honour Notion limits -----------------------------------------------------------


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_long_text_is_clipped_to_2000_units() -> None:
    ev = HackathonEvent(canonical_url="https://a.dev", name="A", description="x" * 3000)
    [chunk] = event_to_properties(ev)["Description"]["rich_text"]
    assert chunk["text"]["content"] == "x" * 2000


def test_clipping_counts_utf16_and_never_splits_emoji() -> None:
    ev = HackathonEvent(canonical_url="https://a.dev", name="A", description="\U0001f680" * 1500)
    [chunk] = event_to_properties(ev)["Description"]["rich_text"]
    assert chunk["text"]["content"] == "\U0001f680" * 1000
    assert _units(chunk["text"]["content"]) == 2000


def test_empty_text_writes_no_text_objects() -> None:
    props = event_to_properties(HackathonEvent(canonical_url="https://a.dev", name="A"))
    assert props["Description"] == {"rich_text": []}
    assert props["Location"] == {"rich_text": []}


def test_multi_select_options_are_sanitised() -> None:
    ev = HackathonEvent(
        canonical_url="https://a.dev",
        name="A",
        themes=["AI, ML", "", "   ", "y" * 150, "AI  ML", "Web3"],
    )
    names = [o["name"] for o in event_to_properties(ev)["Themes"]["multi_select"]]
    assert names == ["AI ML", "y" * 100, "Web3"]
    assert all(n and "," not in n and _units(n) <= 100 for n in names)


def test_select_rejects_empty_and_strips_commas() -> None:
    assert _select("") == {"select": None}
    assert _select(" , ") == {"select": None}
    assert _select("a,b") == {"select": {"name": "a b"}}


def test_date_range_drops_end_not_after_start() -> None:
    ev = HackathonEvent(
        canonical_url="https://a.dev",
        name="A",
        start_date=date(2026, 11, 5),
        end_date=date(2026, 11, 1),
    )
    assert event_to_properties(ev)["Event Dates"] == {"date": {"start": "2026-11-05", "end": None}}
