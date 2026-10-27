from datetime import date

from pydantic import TypeAdapter

from hackathon_sniper.models import (
    Format,
    HackathonEvent,
    LenientDate,
    LenientFormat,
    LenientInt,
    Origin,
)


def test_event_canonicalizes_url() -> None:
    ev = HackathonEvent(canonical_url="https://www.x.devpost.com/?utm_medium=a", name="X")
    assert ev.canonical_url == "https://x.devpost.com"


def test_closing_date_prefers_registration_deadline() -> None:
    ev = HackathonEvent(
        canonical_url="https://a.dev",
        name="A",
        registration_deadline=date(2026, 11, 1),
        end_date=date(2026, 11, 3),
    )
    assert ev.closing_date() == date(2026, 11, 1)
    assert HackathonEvent(
        canonical_url="https://b.dev", name="B", end_date=date(2026, 11, 3)
    ).closing_date() == date(2026, 11, 3)


def test_is_expired() -> None:
    today = date(2026, 10, 26)
    assert HackathonEvent(
        canonical_url="https://a.dev", name="A", registration_deadline=date(2026, 10, 25)
    ).is_expired(today)
    assert not HackathonEvent(
        canonical_url="https://a.dev", name="A", registration_deadline=today
    ).is_expired(today)
    assert not HackathonEvent(canonical_url="https://a.dev", name="A").is_expired(today)


def test_origin_ordering() -> None:
    assert Origin.PAGE > Origin.SOURCE > Origin.SNIPPET


def test_lenient_types_accept_llm_variants() -> None:
    fmt = TypeAdapter(LenientFormat)
    assert fmt.validate_python("in person") == Format.IN_PERSON
    assert fmt.validate_python("ONLINE") == Format.ONLINE
    assert fmt.validate_python(None) == Format.UNKNOWN
    assert fmt.validate_python("on the moon") == Format.UNKNOWN
    assert TypeAdapter(LenientDate).validate_python("TBD") is None
    assert TypeAdapter(LenientDate).validate_python("2026-11-01") == date(2026, 11, 1)
    assert TypeAdapter(LenientInt).validate_python("$10,000") == 10000
    assert TypeAdapter(LenientInt).validate_python("unknown") is None
