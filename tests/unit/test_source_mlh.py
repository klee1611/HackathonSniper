import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from hackathon_sniper.http import make_client
from hackathon_sniper.models import Format, Origin
from hackathon_sniper.sources.mlh import MLHSource, parse_mlh

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sources" / "mlh.html"

EVENT = {
    "name": "hackCBS 9.O",
    "status": "pending",
    "startsAt": "2026-10-31T12:00:00Z",
    "endsAt": "2026-11-01T23:00:00Z",
    "url": "/events/hackcbs-9-o/prizes",
    "location": "New delhi, New delhi",
    "formatType": "physical",
    "websiteUrl": "https://hackcbs.tech",
    "customFields": {"underserved_types": [], "hackathon_focus": []},
}


def page(*events: Any, props: dict[str, Any] | None = None) -> str:
    data = {
        "component": "SeasonEvents/Index",
        "props": props if props is not None else {"upcomingEvents": list(events), "pastEvents": []},
    }
    return (
        f'<html><script data-page="app" type="application/json">{json.dumps(data)}</script></html>'
    )


def test_parse_physical_event() -> None:
    (ev,) = parse_mlh(page(EVENT))
    assert ev.canonical_url == "https://hackcbs.tech"
    assert (ev.start_date, ev.end_date) == (date(2026, 10, 31), date(2026, 11, 1))
    assert (ev.format, ev.location, ev.sources) == (
        Format.IN_PERSON,
        "New delhi, New delhi",
        ["mlh"],
    )
    assert (ev.origin, ev.themes, ev.description) == (Origin.SOURCE, [], "")


def test_digital_event_without_website_uses_mlh_page() -> None:
    (ev,) = parse_mlh(page(EVENT | {"formatType": "digital", "websiteUrl": None}))
    assert (ev.format, ev.location) == (Format.ONLINE, None)
    assert ev.canonical_url == "https://mlh.com/events/hackcbs-9-o/prizes"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("physical", Format.IN_PERSON),
        ("hybrid_physical", Format.HYBRID),
        ("hybrid_digital", Format.HYBRID),
        ("HYBRID", Format.HYBRID),
        ("digital", Format.ONLINE),
        ("something_new", Format.UNKNOWN),
        (None, Format.UNKNOWN),
        (3, Format.UNKNOWN),
    ],
)
def test_format_mapping(raw: object, expected: Format) -> None:
    (ev,) = parse_mlh(page(EVENT | {"formatType": raw}))
    assert ev.format is expected


def test_dates_are_mlh_calendar_dates_not_converted() -> None:
    # MLH's own dateRange matches the stated date even when a US-local reading
    # would fall on the previous day (01:11:11Z is MLH's "no time set" value).
    (ev,) = parse_mlh(
        page(EVENT | {"startsAt": "2027-01-15T01:11:11Z", "endsAt": "2027-01-17T23:59:59Z"})
    )
    assert (ev.start_date, ev.end_date) == (date(2027, 1, 15), date(2027, 1, 17))


@pytest.mark.parametrize("stamp", [None, "", "soon", 12, "2026-13-01T00:00:00Z"])
def test_bad_timestamps_are_none(stamp: object) -> None:
    (ev,) = parse_mlh(page(EVENT | {"startsAt": stamp}))
    assert ev.start_date is None


def test_messy_optional_fields() -> None:
    (ev,) = parse_mlh(
        page(
            EVENT
            | {
                "location": " Cincinnati , Ohio ,",
                "customFields": {"hackathon_focus": ["Software", "", 3, "Hardware"]},
            }
        )
    )
    assert ev.location == "Cincinnati, Ohio"
    assert ev.themes == ["Software", "Hardware"]
    (ev,) = parse_mlh(page(EVENT | {"customFields": None, "location": None}))
    assert (ev.themes, ev.location) == ([], None)
    (ev,) = parse_mlh(page(EVENT | {"customFields": {"hackathon_focus": None}}))
    assert ev.themes == []


@pytest.mark.parametrize("website", ["https://", "", 42, None])
def test_unusable_website_falls_back_to_mlh_page(website: object) -> None:
    (ev,) = parse_mlh(page(EVENT | {"websiteUrl": website}))
    assert ev.canonical_url == "https://mlh.com/events/hackcbs-9-o/prizes"


def test_diversity_focus_is_kept_in_description() -> None:
    focus = ["Women Only", "Underrepresented genders in tech"]
    (ev,) = parse_mlh(page(EVENT | {"customFields": {"underserved_types": focus}}))
    assert ev.description == "MLH diversity focus: Women Only, Underrepresented genders in tech"


@pytest.mark.parametrize(
    "bad",
    [
        "not a dict",
        None,
        EVENT | {"name": ""},
        EVENT | {"name": None},
        EVENT | {"websiteUrl": None, "url": None},
        EVENT | {"websiteUrl": None, "url": "evil.com/x"},  # would become www.mlh.comevil.com
        EVENT | {"websiteUrl": None, "url": "//evil.com/x"},
        EVENT | {"websiteUrl": None, "url": "/\\evil.com"},
        EVENT | {"status": "ended"},
        EVENT | {"customFields": {"underserved_types": ["High School Students Only"]}},
    ],
)
def test_skips_bad_ended_and_high_school_only_items(bad: object) -> None:
    good = EVENT | {"websiteUrl": "https://good.example"}
    assert [e.canonical_url for e in parse_mlh(page(bad, good))] == ["https://good.example"]


def test_structure_change_is_loud() -> None:
    with pytest.raises(ValueError, match="data-page"):
        parse_mlh("<html>redesigned</html>")


@pytest.mark.parametrize("props", [{}, {"upcomingEvents": None}, {"upcomingEvents": {}}])
def test_missing_event_list_is_loud(props: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="upcomingEvents"):
        parse_mlh(page(props=props))


def test_unparseable_json_is_loud() -> None:
    with pytest.raises(ValueError):
        parse_mlh('<script data-page="app" type="application/json">{nope</script>')


def test_parses_recorded_fixture() -> None:
    events = parse_mlh(FIXTURE.read_text())
    by_name = {e.name: e for e in events}
    # The recorded high-school-only event is skipped.
    assert sorted(by_name) == ["ElleHacks", "MakeUC", "hackCBS 9.O"]
    assert all(e.start_date and e.end_date for e in events)
    make_uc = by_name["MakeUC"]
    assert (make_uc.format, make_uc.location) == (Format.HYBRID, "Cincinnati, Ohio")
    assert (make_uc.start_date, make_uc.end_date) == (date(2026, 11, 7), date(2026, 11, 8))
    assert "Women Only" in by_name["ElleHacks"].description


@respx.mock
def test_source_fetches_season_page() -> None:
    respx.get("https://www.mlh.com/seasons/2027/events").mock(
        return_value=httpx.Response(200, html=page(EVENT))
    )
    assert len(MLHSource(make_client(), "2027")()) == 1


@respx.mock
def test_source_raises_on_http_error() -> None:
    respx.get("https://www.mlh.com/seasons/2027/events").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        MLHSource(make_client(), "2027")()
