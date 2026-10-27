from datetime import date, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from hackathon_sniper.models import (
    Format,
    HackathonEvent,
    LenientDate,
    LenientFormat,
    LenientInt,
    LenientSolo,
    Origin,
    Solo,
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$10,000.50", 10000),
        ("10k", 10000),
        ("$1.5M", 1500000),
        ("12.5", 12),
        (True, None),
        ("-1", None),
        (-5, None),
        ("unknown", None),
        (10000.0, 10000),
        (2500, 2500),
    ],
)
def test_lenient_int_takes_first_number(raw: object, expected: int | None) -> None:
    assert TypeAdapter(LenientInt).validate_python(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Nov 1, 2026", date(2026, 11, 1)),
        ("2026-11-01", date(2026, 11, 1)),
        ("2026-11-01T09:00:00Z", date(2026, 11, 1)),
        ("TBD", None),
        (1730000000, None),
        (datetime(2026, 11, 1, 9, 30), date(2026, 11, 1)),
        (date(2026, 11, 1), date(2026, 11, 1)),
        (None, None),
    ],
)
def test_lenient_date_variants(raw: object, expected: date | None) -> None:
    assert TypeAdapter(LenientDate).validate_python(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("virtual", Format.ONLINE),
        ("Remote", Format.ONLINE),
        ("on-site", Format.IN_PERSON),
        ("Offline", Format.IN_PERSON),
        ("in-person", Format.IN_PERSON),
    ],
)
def test_lenient_format_aliases(raw: str, expected: Format) -> None:
    assert TypeAdapter(LenientFormat).validate_python(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", Solo.YES),
        ("Y", Solo.YES),
        ("allowed", Solo.YES),
        (True, Solo.YES),
        ("false", Solo.NO),
        ("n", Solo.NO),
        (False, Solo.NO),
        ("maybe", Solo.UNKNOWN),
    ],
)
def test_lenient_solo_aliases(raw: object, expected: Solo) -> None:
    assert TypeAdapter(LenientSolo).validate_python(raw) == expected


def test_event_rejects_url_without_host() -> None:
    with pytest.raises(ValidationError):
        HackathonEvent(canonical_url="", name="x")
    with pytest.raises(ValidationError):
        HackathonEvent(canonical_url="/path", name="x")
