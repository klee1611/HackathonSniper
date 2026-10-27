import re
from datetime import date, datetime

from dateutil import parser as dateparser

# Parsing twice with defaults that differ in every component reveals missing
# year/month/day: dateutil silently fills those from the default.
_DEFAULTS = (datetime(2000, 1, 1), datetime(2004, 2, 2))
_ISO = re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:[T ].*)?")
_RANGE_SEP = re.compile(r"\s*[-–—]\s*")


def parse_date(text: str | None) -> date | None:
    """Parse a full calendar date; None unless year, month and day are all present."""
    if not text:
        return None
    try:
        first, second = (dateparser.parse(text, default=d).date() for d in _DEFAULTS)
    except (ValueError, OverflowError):
        return None
    return first if first == second else None


def parse_date_range(text: str) -> tuple[date | None, date | None]:
    """Parse Devpost-style ranges: 'Aug 26 - Oct 30, 2026', 'Oct 01 - 15, 2026', 'Nov 3, 2026'."""
    text = text.strip()
    pieces = [text] if _ISO.fullmatch(text) else _RANGE_SEP.split(text, maxsplit=1)
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
    year_injected = not re.search(r"\d{4}", left)
    if year_injected:
        left = f"{left}, {end.year}"
    start = parse_date(left)
    if year_injected and start and start > end:
        try:
            start = start.replace(year=start.year - 1)
        except ValueError:  # Feb 29 has no counterpart in the previous year
            start = None
    return start, end
