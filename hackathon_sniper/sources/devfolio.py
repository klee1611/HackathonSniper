import logging
import re
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import ValidationError

from hackathon_sniper.models import Format, HackathonEvent, Origin, Solo

logger = logging.getLogger(__name__)

# Unofficial search endpoint used by devfolio.co/hackathons.
API = "https://api.devfolio.co/api/search/hackathons"

# The slug becomes a subdomain, so it must be a single DNS label: anything
# else (dots, slashes) could point the event URL at another host.
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)


def _solo(team_min: Any) -> Solo:
    if isinstance(team_min, bool) or not isinstance(team_min, int):
        return Solo.UNKNOWN
    if team_min == 1:
        return Solo.YES
    return Solo.NO if team_min > 1 else Solo.UNKNOWN


def _text(value: Any) -> str | None:
    text = value.strip().rstrip(" ,") if isinstance(value, str) else ""
    return text or None


def _zone(name: Any) -> tzinfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        return UTC


def _date(value: Any, zone: tzinfo, *, closing: bool = False) -> date | None:
    """Local calendar date of an aware ISO timestamp; None if naive or unparseable.

    A close at exactly local midnight means the end of the previous day.
    """
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    if moment is None or moment.tzinfo is None:
        return None
    try:
        local = moment.astimezone(zone)
        if closing and local.time() == time(0):
            return local.date() - timedelta(days=1)
    except OverflowError:  # timestamps at the edges of date.min / date.max
        return None
    return local.date()


def _hits(payload: Any) -> list[Any]:
    hits = payload.get("hits") if isinstance(payload, dict) else None
    items = hits.get("hits") if isinstance(hits, dict) else None
    return items if isinstance(items, list) else []


def _total(payload: Any) -> int | None:
    hits = payload.get("hits") if isinstance(payload, dict) else None
    total = hits.get("total") if isinstance(hits, dict) else None
    value = total.get("value") if isinstance(total, dict) else total
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_source(src: Any) -> HackathonEvent | None:
    if not isinstance(src, dict) or src.get("private"):
        return None
    if src.get("type") not in (None, "HACKATHON"):
        return None
    slug, name = src.get("slug"), _text(src.get("name"))
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or not name:
        return None
    # Devfolio exposes only is_online, so hybrid events land as ONLINE or IN_PERSON
    # here and the Verifier refines them from the event page.
    online = src.get("is_online")
    fmt = (
        Format.ONLINE if online is True else Format.IN_PERSON if online is False else Format.UNKNOWN
    )
    places = (_text(src.get("city")), _text(src.get("country")))
    location = ", ".join(p for p in places if p) or None
    setting = src.get("hackathon_setting")
    zone = _zone(src.get("timezone"))
    themes = src.get("themes")
    return HackathonEvent(
        canonical_url=f"https://{slug}.devfolio.co/",
        name=name,
        sources=["devfolio"],
        registration_deadline=_date(setting.get("reg_ends_at"), zone, closing=True)
        if isinstance(setting, dict)
        else None,
        start_date=_date(src.get("starts_at"), zone),
        end_date=_date(src.get("ends_at"), zone, closing=True),
        format=fmt,
        location=None if fmt is Format.ONLINE else location,
        solo_allowed=_solo(src.get("team_min")),
        themes=[
            t["name"]
            for t in (themes if isinstance(themes, list) else [])
            if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]
        ],
        origin=Origin.SOURCE,
    )


def parse_devfolio(payload: dict[str, Any]) -> list[HackathonEvent]:
    events: list[HackathonEvent] = []
    for hit in _hits(payload):
        src = hit.get("_source") if isinstance(hit, dict) else None
        try:
            event = _parse_source(src)
        except (ValidationError, ValueError, OverflowError, TypeError) as e:
            # One malformed listing must not sink the whole source.
            slug = src.get("slug") if isinstance(src, dict) else None
            logger.info("skipping Devfolio item with slug %r: %s", slug, e)
            continue
        if event is not None:
            events.append(event)
    return events


class DevfolioSource:
    name = "devfolio"

    def __init__(self, client: httpx.Client, size: int = 50) -> None:
        self.client = client
        self.size = size

    def __call__(self) -> list[HackathonEvent]:
        response = self.client.post(
            API, json={"type": "application_open", "from": 0, "size": self.size}
        )
        response.raise_for_status()
        payload = response.json()
        total, fetched = _total(payload), len(_hits(payload))
        if total is not None and total > fetched:
            logger.warning(
                "Devfolio reports %d open hackathons but returned %d; raise size", total, fetched
            )
        return parse_devfolio(payload)
