from datetime import date

import pytest

from hackathon_sniper.dates import parse_date, parse_date_range


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("Aug 26 - Oct 30, 2026", date(2026, 8, 26), date(2026, 10, 30)),
        ("Oct 01 - 15, 2026", date(2026, 10, 1), date(2026, 10, 15)),
        ("Dec 28 - Jan 04, 2027", date(2026, 12, 28), date(2027, 1, 4)),
        ("Nov 3, 2026", date(2026, 11, 3), date(2026, 11, 3)),
        ("", None, None),
        ("Oct 1-15, 2026", date(2026, 10, 1), date(2026, 10, 15)),
        ("Oct 1 — 15, 2026", date(2026, 10, 1), date(2026, 10, 15)),
        ("Oct 1–15, 2026", date(2026, 10, 1), date(2026, 10, 15)),
        ("Oct 30-Nov 2, 2026", date(2026, 10, 30), date(2026, 11, 2)),
        ("2026-10-01", date(2026, 10, 1), date(2026, 10, 1)),
        ("Nov 3 - 5", None, None),
        ("Dec 28, 2026 - Jan 04, 2027", date(2026, 12, 28), date(2027, 1, 4)),
        ("Mar 5, 2027 - Jan 04, 2027", date(2027, 3, 5), date(2027, 1, 4)),
        ("Feb 29 - Feb 1, 2028", None, date(2028, 2, 1)),
    ],
)
def test_parse_date_range(text: str, start: date | None, end: date | None) -> None:
    assert parse_date_range(text) == (start, end)


def test_parse_date_iso_with_offset() -> None:
    assert parse_date("2026-10-28T18:30:00+00:00") == date(2026, 10, 28)


def test_parse_date_garbage_is_none() -> None:
    assert parse_date("Online") is None


@pytest.mark.parametrize("text", ["Nov", "Oct 30", "Monday", "2026", "2026-11"])
def test_parse_date_requires_year_month_and_day(text: str) -> None:
    assert parse_date(text) is None
