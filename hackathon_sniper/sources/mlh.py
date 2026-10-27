import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import ValidationError

from hackathon_sniper.models import Format, HackathonEvent, Origin
from hackathon_sniper.urls import try_canonicalize

logger = logging.getLogger(__name__)

# www.mlh.com renders with Inertia.js; the season page embeds its props as JSON.
URL = "https://www.mlh.com/seasons/{season}/events"
_ORIGIN = "https://www.mlh.com"
# MLH's event links are site-relative paths; anything else could retarget the host.
_PATH = re.compile(r"/(?![/\\])[^\s\\]*")
_LOCATION_COMMA = re.compile(r"\s+,")
# Seen live (2026-10-27): "physical" and "hybrid_physical". Any "hybrid_*" is hybrid.
_FORMATS = {
    "physical": Format.IN_PERSON,
    "digital": Format.ONLINE,
    "online": Format.ONLINE,
    "virtual": Format.ONLINE,
    "hybrid": Format.HYBRID,
}
# Live statuses are "pending" (upcoming) and "ended".
_CLOSED_STATUSES = {"ended", "cancelled", "canceled"}
# The one hard eligibility filter applied at the source: high-school-only events
# are closed to the user outright, while other diversity focuses depend on who
# the user is, so those are kept and surfaced in the description for the Ranker.
_INELIGIBLE = {"high school students only"}


def _text(value: Any) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def _strings(value: Any) -> list[str]:
    return (
        [v.strip() for v in value if isinstance(v, str) and v.strip()]
        if isinstance(value, list)
        else []
    )


def _format(raw: Any) -> Format:
    key = raw.strip().lower() if isinstance(raw, str) else ""
    if key.startswith("hybrid"):
        return Format.HYBRID
    return _FORMATS.get(key, Format.UNKNOWN)


def _date(value: Any) -> date | None:
    """The calendar date as MLH states it.

    MLH gives no event timezone, and its "Z" stamps look like local wall-clock
    times (e.g. 01:11:11Z placeholders, 23:59:59Z ends): the page's own
    dateRange always shows the stated date, so we take it without conversion.
    """
    try:
        return datetime.fromisoformat(value).date() if isinstance(value, str) else None
    except ValueError:
        return None


def _website(value: Any) -> str | None:
    """An organiser's site, if it is a real http(s) URL ("TBA" and the like are not)."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or "." not in (parts.hostname or ""):
        return None
    return try_canonicalize(value)


def _url(item: dict[str, Any]) -> str | None:
    website = _website(item.get("websiteUrl"))
    if website:
        return website
    path = item.get("url")
    return f"{_ORIGIN}{path}" if isinstance(path, str) and _PATH.fullmatch(path) else None


def _parse_item(item: Any) -> HackathonEvent | None:
    if not isinstance(item, dict):
        return None
    name, url = _text(item.get("name")), _url(item)
    if not name or not url:
        return None
    if str(item.get("status", "")).lower() in _CLOSED_STATUSES:
        return None
    custom = item.get("customFields")
    custom = custom if isinstance(custom, dict) else {}
    focus = _strings(custom.get("underserved_types"))
    if _INELIGIBLE.intersection(f.casefold() for f in focus):
        return None
    fmt = _format(item.get("formatType"))
    location = _text(item.get("location"))
    if location:
        location = _LOCATION_COMMA.sub(",", location).rstrip(" ,") or None
    return HackathonEvent(
        canonical_url=url,
        name=name,
        sources=["mlh"],
        start_date=_date(item.get("startsAt")),
        end_date=_date(item.get("endsAt")),
        format=fmt,
        location=None if fmt is Format.ONLINE else location,
        themes=_strings(custom.get("hackathon_focus")),
        description=f"MLH diversity focus: {', '.join(focus)}" if focus else "",
        origin=Origin.SOURCE,
    )


def parse_mlh(html: str) -> list[HackathonEvent]:
    script = BeautifulSoup(html, "html.parser").find("script", attrs={"data-page": "app"})
    if script is None:
        raise ValueError("MLH page has no data-page JSON; the site structure changed")
    data = json.loads(script.get_text())
    props = data.get("props") if isinstance(data, dict) else None
    items = props.get("upcomingEvents") if isinstance(props, dict) else None
    if not isinstance(items, list):
        raise ValueError("MLH page JSON has no upcomingEvents list; the site structure changed")
    events: list[HackathonEvent] = []
    for item in items:
        try:
            event = _parse_item(item)
        except ValidationError as e:
            # One malformed listing must not sink the whole source.
            slug = item.get("slug") or item.get("url") if isinstance(item, dict) else None
            logger.info("skipping MLH item %r: %s", slug, e)
            continue
        if event is not None:
            events.append(event)
    return events


def current_season(today: date) -> str:
    """MLH seasons are named for the year they end; a new one opens around August."""
    return str(today.year + 1 if today.month >= 8 else today.year)


def _utc_today() -> date:
    return datetime.now(UTC).date()


class MLHSource:
    name = "mlh"

    def __init__(
        self,
        client: httpx.Client,
        season: str | None = None,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        self.client = client
        self.season = season
        self.today = today

    def _fetch(self, season: str) -> list[HackathonEvent]:
        response = self.client.get(URL.format(season=season))
        response.raise_for_status()
        return parse_mlh(response.text)

    def _lookahead(self, season: str) -> list[HackathonEvent]:
        try:
            return self._fetch(season)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 404:
                logger.warning("MLH season %s failed, skipping it: %r", season, e)
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("MLH season %s failed, skipping it: %r", season, e)
        return []

    def __call__(self) -> list[HackathonEvent]:
        if self.season is not None:
            return self._fetch(self.season)
        # Derive the season and also peek at the next one, so the source keeps
        # working when MLH opens the next season's page before we roll over.
        season = current_season(self.today())
        events: dict[str, HackathonEvent] = {}
        for event in self._fetch(season) + self._lookahead(str(int(season) + 1)):
            events.setdefault(event.canonical_url, event)
        return list(events.values())
