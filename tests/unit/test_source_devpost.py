import json
import logging
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from hackathon_sniper.http import make_client
from hackathon_sniper.models import Format, Origin
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
        ("€<span data-currency-value>1,000</span>", None),
        ("$<span data-currency-value>0</span>", 0),
        ("", None),
        (None, None),
    ],
)
def test_parse_prize(html: str | None, expected: int | None) -> None:
    assert parse_prize(html) == expected


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


@respx.mock
def test_source_pages_until_empty() -> None:
    route = respx.get(url__startswith=API).mock(
        side_effect=[
            httpx.Response(200, json=PAYLOAD),
            httpx.Response(200, json={"hackathons": []}),
        ]
    )
    events = DevpostSource(make_client(), pages=3)()
    assert len(events) == 2 and route.call_count == 2
    params = route.calls[0].request.url.params
    assert params.get_list("status[]") == ["upcoming", "open"]
    assert params["page"] == "1"


@respx.mock
def test_source_keeps_paging_past_a_page_of_only_invalid_items() -> None:
    bad_page = {"hackathons": [{**PAYLOAD["hackathons"][0], "url": ""}]}
    route = respx.get(url__startswith=API).mock(
        side_effect=[httpx.Response(200, json=bad_page), httpx.Response(200, json=PAYLOAD)]
    )
    events = DevpostSource(make_client(), pages=2)()
    assert len(events) == 2 and route.call_count == 2
