import re
from datetime import date, datetime

from dateutil import parser as dateparser

_DEFAULT = datetime(2000, 1, 1)


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return dateparser.parse(text, default=_DEFAULT).date()
    except (ValueError, OverflowError):
        return None


def parse_date_range(text: str) -> tuple[date | None, date | None]:
    """Parse Devpost-style ranges: 'Aug 26 - Oct 30, 2026', 'Oct 01 - 15, 2026', 'Nov 3, 2026'."""
    pieces = re.split(r"\s+[-–]\s+", text.strip(), maxsplit=1)
    if len(pieces) == 1:
        single = parse_date(pieces[0])
        return single, single
    left, right = pieces
    month = re.match(r"[A-Za-z]+", left)
    if month and re.fullmatch(r"\d{1,2},\s*\d{4}", right):
        right = f"{month.group(0)} {right}"
    end = parse_date(right)
    if end is None:
        return None, None
    if not re.search(r"\d{4}", left):
        left = f"{left}, {end.year}"
    start = parse_date(left)
    if start and start > end:
        start = start.replace(year=start.year - 1)
    return start, end
