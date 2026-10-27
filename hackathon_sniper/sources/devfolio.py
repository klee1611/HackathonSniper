import logging
import re
from datetime import date
from typing import Any

import httpx
from pydantic import ValidationError

from hackathon_sniper.dates import parse_date
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


def _date(value: Any) -> date | None:
    return parse_date(value) if isinstance(value, str) else None


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
    slug, name = src.get("slug"), _text(src.get("name"))
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug) or not name:
        return None
    online = src.get("is_online")
    fmt = (
        Format.ONLINE if online is True else Format.IN_PERSON if online is False else Format.UNKNOWN
    )
    places = (_text(src.get("city")), _text(src.get("country")))
    location = ", ".join(p for p in places if p) or None
    setting = src.get("hackathon_setting")
    themes = src.get("themes")
    return HackathonEvent(
        canonical_url=f"https://{slug}.devfolio.co/",
        name=name,
        sources=["devfolio"],
        registration_deadline=_date(setting.get("reg_ends_at"))
        if isinstance(setting, dict)
        else None,
        start_date=_date(src.get("starts_at")),
        end_date=_date(src.get("ends_at")),
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
        except ValidationError as e:
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
