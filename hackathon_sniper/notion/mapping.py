"""Translate between models and Notion property JSON.

Agents only ever write the columns in HACKATHON_SCHEMA. Any column the user adds is untouched,
because Notion's PATCH /pages only changes the properties it is given.

Readers consume hand-edited Notion data, so they never trust a property's shape: a missing
column, a null value, a column whose type the user changed (select -> multi_select -> text),
an unknown select option or a float in a number column all degrade to a sensible default
instead of raising. Numbers are rounded to the nearest int (round-half-to-even, so a Fit Score
of 72.6 reads as 73 and 72.5 as 72).
"""

import logging
import math
import re
from datetime import date
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

from hackathon_sniper.models import (
    Format,
    HackathonEvent,
    Lifecycle,
    Origin,
    Participation,
    Profile,
    Solo,
    TeamPreference,
    Verification,
)
from hackathon_sniper.notion.client import _plain_text

logger = logging.getLogger(__name__)

# canonicalize() only rejects an empty host, so "not a url" would become "https://not a url".
_HOST = re.compile(r"[^\s/:.]+(\.[^\s/:.]+)+(:\d+)?")

NAME = "Name"
URL = "URL"
SOURCES = "Sources"
DEADLINE = "Registration Deadline"
DATES = "Event Dates"
PRIZE = "Prize USD"
FORMAT = "Format"
LOCATION = "Location"
SOLO = "Solo"
THEMES = "Themes"
DESCRIPTION = "Description"
FIT = "Fit Score"
REASON = "Fit Reason"
VERIFICATION = "Verification"
VERIFIED_AT = "Verified At"
LAST_SEEN = "Last Seen"
LIFECYCLE = "Lifecycle"

HACKATHON_SCHEMA: dict[str, dict[str, Any]] = {
    NAME: {"title": {}},
    URL: {"url": {}},
    SOURCES: {"multi_select": {}},
    DEADLINE: {"date": {}},
    DATES: {"date": {}},
    PRIZE: {"number": {"format": "dollar"}},
    FORMAT: {"select": {}},
    LOCATION: {"rich_text": {}},
    SOLO: {"select": {}},
    THEMES: {"multi_select": {}},
    DESCRIPTION: {"rich_text": {}},
    FIT: {"number": {}},
    REASON: {"rich_text": {}},
    VERIFICATION: {"select": {}},
    VERIFIED_AT: {"date": {}},
    LAST_SEEN: {"date": {}},
    LIFECYCLE: {"select": {}},
}

PROFILE_SCHEMA: dict[str, dict[str, Any]] = {
    "Name": {"title": {}},
    "Skills": {"multi_select": {}},
    "Interests": {"multi_select": {}},
    "Format": {
        "multi_select": {"options": [{"name": f.value} for f in Format if f != Format.UNKNOWN]}
    },
    "Team": {"select": {"options": [{"name": t.value} for t in TeamPreference]}},
    "Min Prize": {"number": {"format": "dollar"}},
    "Location": {"rich_text": {}},
}


def participation_schema(hackathons_db_id: str) -> dict[str, dict[str, Any]]:
    outcomes = ("Submitted", "Finalist", "Winner", "Did not submit")
    return {
        "Project": {"title": {}},
        "Hackathon": {"relation": {"database_id": hackathons_db_id, "single_property": {}}},
        "Team": {"rich_text": {}},
        "Role": {"select": {}},
        "Tech Stack": {"multi_select": {}},
        "Submission URL": {"url": {}},
        "Outcome": {"select": {"options": [{"name": o} for o in outcomes]}},
        "Notes": {"rich_text": {}},
    }


# ---- writers -------------------------------------------------------------------------------

# Notion measures string limits in UTF-16 code units (JavaScript string length).
_MAX_TEXT = 2000
_MAX_OPTION = 100


def _clip(value: str, limit: int) -> str:
    """Cut `value` to at most `limit` UTF-16 code units without splitting a surrogate pair."""
    if len(value.encode("utf-16-le")) // 2 <= limit:
        return value
    units = 0
    for i, char in enumerate(value):
        units += 2 if ord(char) > 0xFFFF else 1
        if units > limit:
            return value[:i]
    return value


def _text(value: str | None) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": _clip(value, _MAX_TEXT)}}] if value else []


def _date(start: date | None, end: date | None = None) -> dict[str, Any]:
    if start is None:
        return {"date": None}
    # Notion rejects an end before the start; a one-day range is just a start.
    end_iso = end.isoformat() if end is not None and end > start else None
    return {"date": {"start": start.isoformat(), "end": end_iso}}


def _option(value: str | None) -> str:
    """A valid select/multi_select option name: no commas, <=100 units, may be empty."""
    if not value:
        return ""
    return _clip(" ".join(value.replace(",", " ").split()), _MAX_OPTION).strip()


def _select(value: str | None) -> dict[str, Any]:
    name = _option(value)
    return {"select": {"name": name} if name else None}


def _multi(values: list[str]) -> dict[str, Any]:
    names = dict.fromkeys(name for name in map(_option, values) if name)
    return {"multi_select": [{"name": name} for name in names]}


def event_to_properties(ev: HackathonEvent) -> dict[str, Any]:
    return {
        NAME: {"title": _text(ev.name)},
        URL: {"url": ev.canonical_url},
        SOURCES: _multi(ev.sources),
        DEADLINE: _date(ev.registration_deadline),
        DATES: _date(ev.start_date or ev.end_date, ev.end_date),
        PRIZE: {"number": ev.prize_usd},
        FORMAT: _select(ev.format.value),
        LOCATION: {"rich_text": _text(ev.location)},
        SOLO: _select(ev.solo_allowed.value),
        THEMES: _multi(ev.themes),
        DESCRIPTION: {"rich_text": _text(ev.description)},
        FIT: {"number": ev.fit_score},
        REASON: {"rich_text": _text(ev.fit_reason)},
        VERIFICATION: _select(ev.verification.value),
        VERIFIED_AT: _date(ev.verified_at),
        LAST_SEEN: _date(ev.last_seen),
        LIFECYCLE: _select(ev.lifecycle.value),
    }


def profile_to_properties(profile: Profile) -> dict[str, Any]:
    return {
        "Name": {"title": _text("My hackathon profile")},
        "Skills": _multi(profile.skills),
        "Interests": _multi(profile.interests),
        "Format": _multi([f.value for f in profile.formats]),
        "Team": _select(profile.team.value),
        "Min Prize": {"number": profile.min_prize_usd},
        "Location": {"rich_text": _text(profile.location)},
    }


# ---- readers -------------------------------------------------------------------------------


def _prop(page: dict[str, Any], name: str) -> dict[str, Any]:
    props = page.get("properties") if isinstance(page, dict) else None
    value = props.get(name) if isinstance(props, dict) else None
    return value if isinstance(value, dict) else {}


def _option_name(option: Any) -> str:
    name = option.get("name") if isinstance(option, dict) else None
    return name.strip() if isinstance(name, str) else ""


def _rich(value: Any) -> str:
    return _plain_text(value) if isinstance(value, list) else ""


def _read_text(prop: dict[str, Any]) -> str:
    """Text from any property type, so a column whose type changed still reads."""
    for key in ("title", "rich_text"):
        if prop.get(key):
            return _rich(prop[key]).strip()
    if isinstance(prop.get("url"), str):
        return str(prop["url"]).strip()
    for key in ("select", "status"):
        if isinstance(prop.get(key), dict):
            return _option_name(prop[key])
    if isinstance(prop.get("multi_select"), list):
        return ", ".join(n for n in map(_option_name, prop["multi_select"]) if n)
    number = prop.get("number")
    if isinstance(number, int | float) and not isinstance(number, bool):
        return str(number)
    return ""


def _read_date(prop: dict[str, Any], key: str = "start") -> date | None:
    """A date from a Notion date property; full datetimes keep their (local) calendar date."""
    raw = prop.get("date")
    value = raw.get(key) if isinstance(raw, dict) else None
    if value is None and key == "start" and not isinstance(raw, dict):
        value = _read_text(prop) or None  # column changed to text: accept "2026-11-01"
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _read_select(prop: dict[str, Any]) -> str | None:
    for key in ("select", "status"):
        if isinstance(prop.get(key), dict):
            return _option_name(prop[key]) or None
    multi = prop.get("multi_select")
    if isinstance(multi, list):
        return next((n for n in map(_option_name, multi) if n), None)
    return _read_text(prop) or None


def _read_multi(prop: dict[str, Any]) -> list[str]:
    multi = prop.get("multi_select")
    if isinstance(multi, list):
        names = [_option_name(o) for o in multi]
    else:
        names = (_read_text(prop) if "number" not in prop else "").split(",")
    return list(dict.fromkeys(n.strip() for n in names if n.strip()))


def _read_int(prop: dict[str, Any]) -> int | None:
    number = prop.get("number")
    if number is None and "number" not in prop:
        text = _read_text(prop).replace(",", "").replace("$", "").strip()
        try:
            number = float(text) if text else None
        except ValueError:
            return None
    if isinstance(number, bool) or not isinstance(number, int | float):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return round(number)


def _enum[E: Enum](cls: type[E], value: str | None, default: E) -> E:
    if not value:
        return default
    wanted = value.strip().casefold()
    return next((m for m in cls if str(m.value).casefold() == wanted), default)


def page_to_event(page: dict[str, Any]) -> HackathonEvent | None:
    """The event stored in a Hackathons row, or None (logged) if the row is unusable."""
    page_id = page.get("id") if isinstance(page, dict) else None
    try:
        return _page_to_event(page)
    except Exception as exc:  # one odd hand-edited row must never crash the run
        logger.warning("Skipping Notion page %s: %s", page_id, exc)
        return None


def _page_to_event(page: dict[str, Any]) -> HackathonEvent | None:
    url = _read_text(_prop(page, URL))
    name = _read_text(_prop(page, NAME))
    if not url or not name:
        logger.warning("Skipping Notion page %s: missing name or URL", page.get("id"))
        return None
    verification = _enum(
        Verification, _read_select(_prop(page, VERIFICATION)), Verification.UNVERIFIED
    )
    dates = _prop(page, DATES)
    page_id = page.get("id")
    event = HackathonEvent(
        canonical_url=url,
        name=name,
        sources=_read_multi(_prop(page, SOURCES)),
        registration_deadline=_read_date(_prop(page, DEADLINE)),
        start_date=_read_date(dates),
        end_date=_read_date(dates, "end") or _read_date(dates),
        prize_usd=_read_int(_prop(page, PRIZE)),
        format=_enum(Format, _read_select(_prop(page, FORMAT)), Format.UNKNOWN),
        location=_read_text(_prop(page, LOCATION)) or None,
        solo_allowed=_enum(Solo, _read_select(_prop(page, SOLO)), Solo.UNKNOWN),
        themes=_read_multi(_prop(page, THEMES)),
        description=_read_text(_prop(page, DESCRIPTION)),
        origin=Origin.PAGE if verification != Verification.UNVERIFIED else Origin.SNIPPET,
        verification=verification,
        verified_at=_read_date(_prop(page, VERIFIED_AT)),
        last_seen=_read_date(_prop(page, LAST_SEEN)),
        fit_score=_read_int(_prop(page, FIT)),
        fit_reason=_read_text(_prop(page, REASON)) or None,
        lifecycle=_enum(Lifecycle, _read_select(_prop(page, LIFECYCLE)), Lifecycle.ACTIVE),
        notion_page_id=str(page_id) if page_id else None,
    )
    if not _HOST.fullmatch(urlsplit(event.canonical_url).netloc):
        logger.warning("Skipping Notion page %s: implausible URL %r", page_id, url)
        return None
    return event


def page_to_profile(page: dict[str, Any], body: str) -> Profile:
    formats = [_enum(Format, v, Format.UNKNOWN) for v in _read_multi(_prop(page, "Format"))]
    return Profile(
        skills=_read_multi(_prop(page, "Skills")),
        interests=_read_multi(_prop(page, "Interests")),
        formats=list(dict.fromkeys(f for f in formats if f != Format.UNKNOWN)),
        team=_enum(TeamPreference, _read_select(_prop(page, "Team")), TeamPreference.EITHER),
        min_prize_usd=_read_int(_prop(page, "Min Prize")),
        location=_read_text(_prop(page, "Location")) or None,
        notes=(body or "").strip(),
    )


def page_to_participation(page: dict[str, Any], names_by_id: dict[str, str]) -> Participation:
    relation = _prop(page, "Hackathon").get("relation")
    links = relation if isinstance(relation, list) else []
    related = [r.get("id") for r in links if isinstance(r, dict)]
    return Participation(
        hackathon=next((names_by_id[r] for r in related if r in names_by_id), None),
        project=_read_text(_prop(page, "Project")),
        role=_read_select(_prop(page, "Role")),
        tech_stack=_read_multi(_prop(page, "Tech Stack")),
        outcome=_read_select(_prop(page, "Outcome")),
        notes=_read_text(_prop(page, "Notes")),
    )
