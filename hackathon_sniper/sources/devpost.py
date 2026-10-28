import logging
import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from hackathon_sniper.dates import parse_date_range
from hackathon_sniper.models import Format, HackathonEvent, Origin

logger = logging.getLogger(__name__)

# Unofficial JSON endpoint behind devpost.com/hackathons.
API = "https://devpost.com/api/hackathons"

_USD = re.compile(r"\s*\$\s*([\d,]+)\s*")
_ONLINE_SUFFIX = re.compile(r"\s*\+\s*online\s*$", re.IGNORECASE)


def parse_prize(html: str | None) -> int | None:
    """'$<span>50,000</span>' -> 50000.

    Only a bare dollar amount counts: other dollar currencies (CA$, MEX$, $CAD),
    compound or fractional amounts are ambiguous and come back as None.
    """
    if not html:
        return None
    match = _USD.fullmatch(re.sub(r"<[^>]+>", "", html))
    digits = match.group(1).replace(",", "") if match else ""
    return int(digits) if digits else None


def _format_and_location(raw: Any) -> tuple[Format, str | None]:
    location = raw.strip().rstrip(" ,") if isinstance(raw, str) else ""
    lowered = location.lower()
    if not location:
        return Format.UNKNOWN, None
    if lowered.startswith("online"):
        return Format.ONLINE, None
    if "+ online" in lowered or "hybrid" in lowered:
        venue = _ONLINE_SUFFIX.sub("", location).rstrip(" ,")
        return Format.HYBRID, venue or None
    return Format.IN_PERSON, location


def _items(payload: Any) -> list[Any]:
    items = payload.get("hackathons") if isinstance(payload, dict) else None
    return items if isinstance(items, list) else []


def _parse_item(item: Any) -> HackathonEvent | None:
    if not isinstance(item, dict) or not item.get("url") or not item.get("title"):
        return None
    if item.get("invite_only"):
        return None
    dates = item.get("submission_period_dates")
    start, end = parse_date_range(dates) if isinstance(dates, str) else (None, None)
    displayed = item.get("displayed_location")
    fmt, location = _format_and_location(
        displayed.get("location") if isinstance(displayed, dict) else None
    )
    themes = item.get("themes")
    prize = item.get("prize_amount")
    return HackathonEvent(
        canonical_url=item["url"],
        name=str(item["title"]),
        sources=["devpost"],
        registration_deadline=end,  # Devpost registration stays open until submissions close
        start_date=start,
        end_date=end,
        prize_usd=parse_prize(prize) if isinstance(prize, str) else None,
        format=fmt,
        location=location,
        themes=[
            t["name"]
            for t in (themes if isinstance(themes, list) else [])
            if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]
        ],
        origin=Origin.SOURCE,
    )


def _parse_items(items: list[Any]) -> list[HackathonEvent]:
    events: list[HackathonEvent] = []
    for item in items:
        try:
            event = _parse_item(item)
        except ValidationError as e:
            # One malformed listing must not sink the whole source.
            url = item.get("url") if isinstance(item, dict) else None
            logger.info("skipping Devpost item with url %r: %s", url, e)
            continue
        if event is not None:
            events.append(event)
    return events


def parse_devpost(payload: dict[str, Any]) -> list[HackathonEvent]:
    return _parse_items(_items(payload))


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _is_last_page(payload: Any, page: int, page_size: int) -> bool:
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        return False
    total = _positive_int(meta.get("total_count"))
    per_page = _positive_int(meta.get("per_page")) or page_size
    return total is not None and page * per_page >= total


class DevpostSource:
    name = "devpost"

    def __init__(
        self,
        client: httpx.Client,
        pages: int = 25,
        sleep: Callable[[float], None] = time.sleep,
        delay: float = 0.5,
    ) -> None:
        self.client = client
        self.pages = pages
        self.sleep = sleep
        self.delay = delay

    def _fetch(self, page: int) -> Any:
        response = self.client.get(
            API, params=[("status[]", "upcoming"), ("status[]", "open"), ("page", page)]
        )
        response.raise_for_status()
        return response.json()

    def __call__(self) -> list[HackathonEvent]:
        # Devpost lists every open event before the upcoming ones, so a few
        # pages are not enough; page until the reported total is covered.
        events: dict[str, HackathonEvent] = {}
        for page in range(1, self.pages + 1):
            if page > 1:
                self.sleep(self.delay)
                try:
                    payload = self._fetch(page)
                except (httpx.HTTPError, ValueError) as e:
                    logger.warning(
                        "Devpost page %d failed, keeping %d events: %r", page, len(events), e
                    )
                    break
            else:
                payload = self._fetch(page)
            # Stop on an empty raw page, not an empty parsed one: a page of
            # only malformed items says nothing about whether later pages exist.
            items = _items(payload)
            if not items:
                break
            parsed = _parse_items(items)
            new = [e for e in parsed if e.canonical_url not in events]
            for event in new:
                events[event.canonical_url] = event
            if parsed and not new:
                break  # the API is repeating itself (e.g. ignoring `page`)
            if _is_last_page(payload, page, len(items)):
                break
        return list(events.values())
