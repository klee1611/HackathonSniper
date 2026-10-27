import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from hackathon_sniper.dates import parse_date_range
from hackathon_sniper.models import Format, HackathonEvent, Origin

logger = logging.getLogger(__name__)

# Unofficial JSON endpoint behind devpost.com/hackathons.
API = "https://devpost.com/api/hackathons"


def parse_prize(html: str | None) -> int | None:
    """'$<span>50,000</span>' -> 50000. Non-USD amounts are ignored."""
    if not html:
        return None
    text = re.sub(r"<[^>]+>", "", html)
    if "$" not in text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _items(payload: Any) -> list[Any]:
    items = payload.get("hackathons") if isinstance(payload, dict) else None
    return items if isinstance(items, list) else []


def _parse_item(item: Any) -> HackathonEvent | None:
    if not isinstance(item, dict) or not item.get("url") or not item.get("title"):
        return None
    dates = item.get("submission_period_dates")
    start, end = parse_date_range(dates) if isinstance(dates, str) else (None, None)
    displayed = item.get("displayed_location")
    raw_location = displayed.get("location") if isinstance(displayed, dict) else None
    location = raw_location.strip() if isinstance(raw_location, str) else ""
    online = location.lower() == "online"
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
        format=Format.ONLINE if online else Format.IN_PERSON if location else Format.UNKNOWN,
        location=None if online or not location else location,
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


class DevpostSource:
    name = "devpost"

    def __init__(self, client: httpx.Client, pages: int = 3) -> None:
        self.client = client
        self.pages = pages

    def __call__(self) -> list[HackathonEvent]:
        events: list[HackathonEvent] = []
        for page in range(1, self.pages + 1):
            response = self.client.get(
                API, params=[("status[]", "upcoming"), ("status[]", "open"), ("page", page)]
            )
            response.raise_for_status()
            # Stop on an empty raw page, not an empty parsed one: a page of
            # only malformed items says nothing about whether later pages exist.
            items = _items(response.json())
            if not items:
                break
            events += _parse_items(items)
        return events
