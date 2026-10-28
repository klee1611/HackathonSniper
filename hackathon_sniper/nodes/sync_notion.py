"""Upsert ranked events into the Notion Hackathons database.

Rules:
- Keyed by canonical URL. A URL already in `existing` always updates that row (its page id
  wins over whatever the event carries), so a row is never duplicated.
- Lifecycle is recomputed from dates: Expired iff the closing date is before today, so an
  extended deadline re-activates a row. Archiving is just Active -> Expired.
- Already-closed events never get a new row.
- Only agent-owned columns are sent; PATCH keeps the user's own columns.
- Write budget: a row whose only change is Last Seen is skipped unless the stored Last Seen is
  missing or LAST_SEEN_REFRESH_DAYS old, so seen-but-unchanged rows aren't PATCHed daily.
- Dry run counts but never writes. One failed row is counted and reported, never fatal.

No pacing here: NotionGateway retries 429s honouring Retry-After.
"""

import logging
from datetime import date
from typing import Any

from hackathon_sniper.deps import Deps
from hackathon_sniper.models import HackathonEvent, Lifecycle
from hackathon_sniper.notion.mapping import LAST_SEEN, event_to_properties
from hackathon_sniper.state import GraphState, SyncResult

logger = logging.getLogger(__name__)

LAST_SEEN_REFRESH_DAYS = 7
# Errors end up in the (public) job summary; NotionError text is already token-scrubbed.
MAX_ERROR_CHARS = 300


def _without_last_seen(properties: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in properties.items() if k != LAST_SEEN}


def _last_seen_stale(old: date | None, new: date | None) -> bool:
    if new is None or old == new:
        return False
    return old is None or (new - old).days >= LAST_SEEN_REFRESH_DAYS


def _needs_write(old: HackathonEvent, new: HackathonEvent, properties: dict[str, Any]) -> bool:
    if _without_last_seen(event_to_properties(old)) != _without_last_seen(properties):
        return True
    return _last_seen_stale(old.last_seen, new.last_seen)


def _error(url: str, exc: Exception) -> str:
    message = f"sync {url}: {type(exc).__name__}: {exc}"
    return message if len(message) <= MAX_ERROR_CHARS else message[: MAX_ERROR_CHARS - 1] + "…"


def sync_notion(state: GraphState, deps: Deps) -> dict[str, Any]:
    """Upsert keyed by URL. Archive by date only — never because a row wasn't seen today."""
    s = deps.settings
    existing = state.get("existing", {})
    result = SyncResult()
    errors: list[str] = []
    done: set[str] = set()
    # Stable order so writes and errors are reproducible run to run.
    for ev in sorted(state.get("ranked", []), key=lambda e: e.canonical_url):
        url = ev.canonical_url
        if url in done:
            logger.warning("Skipping duplicate ranked event for %s", url)
            continue
        done.add(url)
        old = existing.get(url)
        page_id = (old.notion_page_id if old is not None else None) or ev.notion_page_id
        if old is not None and ev.notion_page_id not in (None, page_id):
            logger.warning(
                "Event %s carries page id %s; updating existing row %s instead",
                url,
                ev.notion_page_id,
                page_id,
            )
        lifecycle = Lifecycle.EXPIRED if ev.is_expired(deps.today) else Lifecycle.ACTIVE
        ev = ev.model_copy(update={"lifecycle": lifecycle, "notion_page_id": page_id})
        try:
            properties = event_to_properties(ev)
            if page_id is None:
                if lifecycle == Lifecycle.EXPIRED:
                    continue  # never create rows for events that already closed
                if not s.dry_run:
                    deps.notion.create_page(s.notion_hackathons_db_id, properties)
                result.created += 1
                continue
            if old is not None and not _needs_write(old, ev, properties):
                result.unchanged += 1
                continue
            if not s.dry_run:
                deps.notion.update_page(page_id, properties)
            if lifecycle == Lifecycle.EXPIRED and (
                old is None or old.lifecycle == Lifecycle.ACTIVE
            ):
                result.archived += 1
            else:
                result.updated += 1
        except Exception as e:  # one bad row must never abort the sync
            message = _error(url, e)
            logger.error("%s", message)
            result.failed += 1
            errors.append(message)
    return {"sync_result": result, "errors": errors}
