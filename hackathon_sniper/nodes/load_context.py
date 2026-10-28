import logging
from datetime import date
from typing import Any

from hackathon_sniper.deps import Deps
from hackathon_sniper.models import HackathonEvent, Lifecycle
from hackathon_sniper.notion.mapping import page_to_event, page_to_participation, page_to_profile
from hackathon_sniper.state import GraphState

logger = logging.getLogger(__name__)


def _preference(ev: HackathonEvent) -> tuple[bool, date]:
    """Rank duplicate rows: an Active row beats an Expired one, then the most recently
    seen or verified wins. Undated rows rank lowest; ties keep the first row seen."""
    dates = [d for d in (ev.verified_at, ev.last_seen) if d is not None]
    return ev.lifecycle == Lifecycle.ACTIVE, max(dates, default=date.min)


def load_context(state: GraphState, deps: Deps) -> dict[str, Any]:
    """Read profile, existing rows, and history up front. Failures here abort the run
    (NotionError propagates): syncing without knowing existing rows would create duplicates."""
    s = deps.settings
    profile = page_to_profile(
        deps.notion.get_page(s.notion_profile_page_id),
        deps.notion.get_page_text(s.notion_profile_page_id),
    )
    existing: dict[str, HackathonEvent] = {}
    names_by_id: dict[str, str] = {}
    for page in deps.notion.query_all(s.notion_hackathons_db_id):
        ev = page_to_event(page)
        if ev is None:
            continue
        if ev.notion_page_id:
            # Every readable row, duplicates included, so any participation link resolves.
            names_by_id[ev.notion_page_id] = ev.name
        kept = existing.get(ev.canonical_url)
        if kept is None:
            existing[ev.canonical_url] = ev
            continue
        winner = ev if _preference(ev) > _preference(kept) else kept
        logger.warning(
            "Duplicate Notion rows for %s (%s, %s); keeping %s",
            ev.canonical_url,
            kept.notion_page_id,
            ev.notion_page_id,
            winner.notion_page_id,
        )
        existing[ev.canonical_url] = winner
    history = [
        page_to_participation(page, names_by_id)
        for page in deps.notion.query_all(s.notion_participations_db_id)
    ]
    return {"profile": profile, "existing": existing, "history": history}
