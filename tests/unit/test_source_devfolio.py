import json
import logging
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from hackathon_sniper.http import make_client
from hackathon_sniper.models import Format, Origin, Solo
from hackathon_sniper.sources.devfolio import API, DevfolioSource, parse_devfolio

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sources" / "devfolio.json"


def hit(**source: object) -> dict[str, object]:
    base = {
        "name": "Surat Hack 2026",
        "slug": "surat-hack-2026",
        "starts_at": "2026-11-01T00:30:00+00:00",
        "ends_at": "2026-11-01T18:30:00+00:00",
        "is_online": False,
        "city": "Surat",
        "country": "India",
        "team_min": 1,
        "themes": [{"name": "AI"}],
        "hackathon_setting": {"reg_ends_at": "2026-10-28T18:30:00+00:00"},
    }
    return {"_source": base | source}


def test_parse_devfolio() -> None:
    (ev,) = parse_devfolio({"hits": {"hits": [hit()]}})
    assert ev.canonical_url == "https://surat-hack-2026.devfolio.co"
    assert ev.registration_deadline == date(2026, 10, 28)
    assert (ev.start_date, ev.end_date) == (date(2026, 11, 1), date(2026, 11, 1))
    assert (ev.format, ev.location, ev.solo_allowed, ev.themes) == (
        Format.IN_PERSON,
        "Surat, India",
        Solo.YES,
        ["AI"],
    )
    assert (ev.sources, ev.origin) == (["devfolio"], Origin.SOURCE)


def test_online_and_team_only() -> None:
    (ev,) = parse_devfolio({"hits": {"hits": [hit(is_online=True, team_min=2)]}})
    assert (ev.format, ev.location, ev.solo_allowed) == (Format.ONLINE, None, Solo.NO)


def test_missing_optional_fields_are_unknown() -> None:
    (ev,) = parse_devfolio(
        {
            "hits": {
                "hits": [
                    hit(
                        is_online=None,
                        city=None,
                        country="United Kingdom ,",
                        team_min=True,
                        themes=[{"name": ""}, "AI", {"name": 3}, {"name": "Web3"}],
                        hackathon_setting=None,
                        starts_at="soon",
                        ends_at=12,
                    )
                ]
            }
        }
    )
    assert (ev.format, ev.location, ev.solo_allowed) == (
        Format.UNKNOWN,
        "United Kingdom",
        Solo.UNKNOWN,
    )
    assert (ev.registration_deadline, ev.start_date, ev.end_date) == (None, None, None)
    assert ev.themes == ["Web3"]


def test_null_registration_deadline() -> None:
    (ev,) = parse_devfolio({"hits": {"hits": [hit(hackathon_setting={"reg_ends_at": None})]}})
    assert ev.registration_deadline is None


@pytest.mark.parametrize(
    "bad",
    [
        "not a dict",
        {"_source": "nope"},
        hit(slug=None),
        hit(name=""),
        hit(private=True),
        hit(slug="evil.com/x"),
        hit(slug="-bad-"),
        hit(slug=42),
    ],
)
def test_skips_bad_and_private_items(bad: object) -> None:
    payload = {"hits": {"hits": [bad, hit(slug="good")]}}
    assert [e.canonical_url for e in parse_devfolio(payload)] == ["https://good.devfolio.co"]


@pytest.mark.parametrize("payload", [{}, {"hits": None}, {"hits": {"hits": "x"}}, []])
def test_malformed_payload_is_empty(payload: object) -> None:
    assert parse_devfolio(payload) == []  # type: ignore[arg-type]


def test_parses_recorded_fixture() -> None:
    events = parse_devfolio(json.loads(FIXTURE.read_text()))
    assert len(events) == 3
    assert all(e.canonical_url.endswith(".devfolio.co") for e in events)
    assert all(e.start_date and e.end_date and e.registration_deadline for e in events)


@respx.mock
def test_source_posts_open_application_query() -> None:
    route = respx.post(API).mock(return_value=httpx.Response(200, json={"hits": {"hits": [hit()]}}))
    assert len(DevfolioSource(make_client())()) == 1
    body = json.loads(route.calls.last.request.content)
    assert body == {"type": "application_open", "from": 0, "size": 50}


@respx.mock
def test_source_warns_when_total_exceeds_page(caplog: pytest.LogCaptureFixture) -> None:
    payload = {"hits": {"total": {"value": 5}, "hits": [hit()]}}
    respx.post(API).mock(return_value=httpx.Response(200, json=payload))
    with caplog.at_level(logging.WARNING, logger="hackathon_sniper.sources.devfolio"):
        assert len(DevfolioSource(make_client(), size=1)()) == 1
    assert "5" in caplog.text


@respx.mock
def test_source_raises_on_http_error() -> None:
    respx.post(API).mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        DevfolioSource(make_client())()
