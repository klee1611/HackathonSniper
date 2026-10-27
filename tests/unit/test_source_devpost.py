import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from hackathon_sniper.http import make_client
from hackathon_sniper.models import Format, HackathonEvent, Origin
from hackathon_sniper.sources.devpost import API, DevpostSource, parse_devpost, parse_prize

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sources" / "devpost.json"

PAYLOAD = {
    "hackathons": [
        {
            "title": "Nebius x NVIDIA Global AI Hackathon",
            "url": "https://nebiusglobalaihackathon.devpost.com/",
            "submission_period_dates": "Aug 26 - Oct 30, 2026",
            "displayed_location": {"icon": "globe", "location": "Online"},
            "themes": [{"id": 6, "name": "Machine Learning/AI"}],
            "prize_amount": "$<span data-currency-value>50,000</span>",
        },
        {
            "title": "Campus Hack",
            "url": "https://campushack.devpost.com/",
            "submission_period_dates": "Nov 7 - 8, 2026",
            "displayed_location": {"icon": "map-marker", "location": "Austin, TX"},
            "themes": [],
            "prize_amount": "€<span data-currency-value>1,000</span>",
        },
    ]
}


def test_parse_devpost() -> None:
    online, campus = parse_devpost(PAYLOAD)
    assert online.canonical_url == "https://nebiusglobalaihackathon.devpost.com"
    assert (online.start_date, online.end_date) == (date(2026, 8, 26), date(2026, 10, 30))
    assert online.registration_deadline == date(2026, 10, 30)
    assert (online.prize_usd, online.format, online.location) == (50000, Format.ONLINE, None)
    assert online.themes == ["Machine Learning/AI"]
    assert (online.sources, online.origin) == (["devpost"], Origin.SOURCE)
    assert (campus.format, campus.location, campus.prize_usd) == (
        Format.IN_PERSON,
        "Austin, TX",
        None,
    )
    assert (campus.start_date, campus.end_date) == (date(2026, 11, 7), date(2026, 11, 8))


def test_parses_recorded_fixture() -> None:
    events = parse_devpost(json.loads(FIXTURE.read_text()))
    assert len(events) == 3
    assert all(e.canonical_url.startswith("https://") for e in events)
    assert all(e.start_date and e.end_date and e.prize_usd for e in events)


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("$<span data-currency-value>1,000,000</span>", 1_000_000),
        ("$0", 0),
        ("$CAD <span data-currency-value>30,000</span>", None),
        ("MEX$ 50,000", None),
        ("CA$5,000", None),
        ("A$2,000", None),
        ("$5,000 + $2,000 in credits", None),
        ("$10.50", None),
        ("\u20b950,000", None),
        ("\u20ac<span data-currency-value>1,000</span>", None),
        ("$,", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_prize(html: str | None, expected: int | None) -> None:
    assert parse_prize(html) == expected


def _one(**overrides: object) -> HackathonEvent:
    item = {**PAYLOAD["hackathons"][0], **overrides}
    (event,) = parse_devpost({"hackathons": [item]})
    return event


@pytest.mark.parametrize(
    ("raw", "fmt", "location"),
    [
        ("Online", Format.ONLINE, None),
        ("  online ", Format.ONLINE, None),
        ("Online, from anywhere", Format.ONLINE, None),
        ("Santa Clara Convention Center + Online", Format.HYBRID, "Santa Clara Convention Center"),
        ("Berlin (Hybrid)", Format.HYBRID, "Berlin (Hybrid)"),
        ("Block 45, Sharda University, ", Format.IN_PERSON, "Block 45, Sharda University"),
        ("Austin, TX", Format.IN_PERSON, "Austin, TX"),
        ("", Format.UNKNOWN, None),
    ],
)
def test_location_format(raw: str, fmt: Format, location: str | None) -> None:
    event = _one(displayed_location={"icon": "map-marker", "location": raw})
    assert (event.format, event.location) == (fmt, location)


def test_skips_invite_only() -> None:
    good = PAYLOAD["hackathons"][0]
    payload = {
        "hackathons": [
            {**good, "url": "https://private.devpost.com/", "invite_only": True},
            {**good, "invite_only": False},
        ]
    }
    assert [e.canonical_url for e in parse_devpost(payload)] == [
        "https://nebiusglobalaihackathon.devpost.com"
    ]


def test_skips_items_with_invalid_url(caplog: pytest.LogCaptureFixture) -> None:
    good = PAYLOAD["hackathons"][0]
    payload = {
        "hackathons": [
            {**good, "url": "/relative"},
            {**good, "url": 42},
            "not a dict",
            {**good, "title": ""},
            good,
        ]
    }
    with caplog.at_level(logging.INFO, logger="hackathon_sniper.sources.devpost"):
        events = parse_devpost(payload)
    assert [e.name for e in events] == [good["title"]]
    assert "/relative" in caplog.text


def test_tolerates_missing_and_malformed_optional_fields() -> None:
    (event,) = parse_devpost(
        {
            "hackathons": [
                {
                    "title": "Bare",
                    "url": "https://bare.devpost.com/",
                    "displayed_location": None,
                    "themes": None,
                    "submission_period_dates": None,
                }
            ]
        }
    )
    assert (event.format, event.location, event.themes) == (Format.UNKNOWN, None, [])
    assert (event.start_date, event.end_date, event.prize_usd) == (None, None, None)


def _page(start: int, count: int, total: int | None = None, per_page: int = 9) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "hackathons": [
            {
                "title": f"Hack {i}",
                "url": f"https://hack{i}.devpost.com/",
                "displayed_location": {"location": "Online"},
            }
            for i in range(start, start + count)
        ]
    }
    if total is not None:
        payload["meta"] = {"total_count": total, "per_page": per_page, "fuzzy": False}
    return payload


def _source(**kwargs: Any) -> DevpostSource:
    return DevpostSource(make_client(), sleep=lambda _: None, **kwargs)


def test_default_pages_cover_all_listings() -> None:
    assert DevpostSource(make_client()).pages == 25


@respx.mock
def test_source_pages_until_empty() -> None:
    route = respx.get(url__startswith=API).mock(
        side_effect=[
            httpx.Response(200, json=PAYLOAD),
            httpx.Response(200, json={"hackathons": []}),
        ]
    )
    events = _source(pages=3)()
    assert len(events) == 2 and route.call_count == 2
    params = route.calls[0].request.url.params
    assert params.get_list("status[]") == ["upcoming", "open"]
    assert [c.request.url.params["page"] for c in route.calls] == ["1", "2"]


@respx.mock
def test_source_stops_at_total_count() -> None:
    route = respx.get(url__startswith=API).mock(
        side_effect=[
            httpx.Response(200, json=_page(0, 9, total=20)),
            httpx.Response(200, json=_page(9, 9, total=20)),
            httpx.Response(200, json=_page(18, 2, total=20)),
            httpx.Response(200, json=_page(99, 9, total=20)),
        ]
    )
    events = _source()()
    assert len(events) == 20 and route.call_count == 3


@respx.mock
def test_source_total_count_falls_back_to_page_size() -> None:
    first = _page(0, 3, total=6)
    del first["meta"]["per_page"]
    route = respx.get(url__startswith=API).mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=_page(3, 3, 6))]
    )
    assert len(_source()()) == 6 and route.call_count == 2


@respx.mock
def test_source_stops_when_api_ignores_page() -> None:
    route = respx.get(url__startswith=API).mock(return_value=httpx.Response(200, json=PAYLOAD))
    events = _source()()
    assert len(events) == 2 and route.call_count == 2


@respx.mock
def test_source_dedupes_across_pages() -> None:
    respx.get(url__startswith=API).mock(
        side_effect=[
            httpx.Response(200, json=_page(0, 3)),
            httpx.Response(200, json=_page(2, 3)),
            httpx.Response(200, json={"hackathons": []}),
        ]
    )
    urls = [e.canonical_url for e in _source()()]
    assert len(urls) == len(set(urls)) == 5


@respx.mock
def test_source_sleeps_between_pages_only() -> None:
    respx.get(url__startswith=API).mock(
        side_effect=[httpx.Response(200, json=_page(i * 2, 2)) for i in range(3)]
    )
    sleeps: list[float] = []
    events = DevpostSource(make_client(), pages=3, sleep=sleeps.append, delay=0.25)()
    assert len(events) == 6 and sleeps == [0.25, 0.25]


@respx.mock
def test_source_keeps_paging_past_a_page_of_only_invalid_items() -> None:
    bad_page = {"hackathons": [{**PAYLOAD["hackathons"][0], "url": ""}]}
    route = respx.get(url__startswith=API).mock(
        side_effect=[httpx.Response(200, json=bad_page), httpx.Response(200, json=PAYLOAD)]
    )
    events = _source(pages=2)()
    assert len(events) == 2 and route.call_count == 2


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(429), httpx.Response(200, text="<html>not json</html>")],
    ids=["rate-limited", "non-json"],
)
@respx.mock
def test_source_returns_partial_results_when_later_page_fails(
    failure: httpx.Response, caplog: pytest.LogCaptureFixture
) -> None:
    respx.get(url__startswith=API).mock(side_effect=[httpx.Response(200, json=PAYLOAD), failure])
    with caplog.at_level(logging.WARNING, logger="hackathon_sniper.sources.devpost"):
        events = _source()()
    assert len(events) == 2
    assert "page 2" in caplog.text


@respx.mock
def test_source_raises_when_first_page_fails() -> None:
    respx.get(url__startswith=API).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        _source()()
