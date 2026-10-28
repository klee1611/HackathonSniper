from datetime import date
from typing import Any

from hackathon_sniper.deps import Deps
from hackathon_sniper.models import Format, HackathonEvent, Lifecycle, Solo
from hackathon_sniper.state import GraphState

MAX_THEMES = 10

_SCALARS = (
    "name",
    "registration_deadline",
    "start_date",
    "end_date",
    "prize_usd",
    "location",
    "description",
)
_ENUMS: dict[str, Any] = {"format": Format.UNKNOWN, "solo_allowed": Solo.UNKNOWN}
# Owned by the Ranker / the user's Notion row; candidates never carry them, so they only fill gaps.
_GAP_ONLY = ("fit_score", "fit_reason")


def _empty(value: Any) -> bool:
    return value is None or value == ""


def _merge_themes(preferred: list[str], other: list[str]) -> list[str]:
    """Case-insensitive union, preferred side's spelling and themes first, capped, sorted."""
    kept: dict[str, str] = {}
    for theme in (*preferred, *other):
        theme = theme.strip()
        key = theme.casefold()
        if theme and key not in kept and len(kept) < MAX_THEMES:
            kept[key] = theme
    return [kept[key] for key in sorted(kept)]


def merge(base: HackathonEvent, new: HackathonEvent) -> HackathonEvent:
    """Combine two views of one event: the stronger origin wins, and nothing is overwritten
    with empty. Starts from `base`, so fields only `base` owns (fit, lifecycle, page id) survive."""
    stronger = new.origin >= base.origin
    update: dict[str, Any] = {}
    for field in _SCALARS:
        value = getattr(new, field)
        if not _empty(value) and (stronger or _empty(getattr(base, field))):
            update[field] = value
    for field, unknown in _ENUMS.items():
        value = getattr(new, field)
        if value != unknown and (stronger or getattr(base, field) == unknown):
            update[field] = value
    for field in _GAP_ONLY:
        if _empty(getattr(base, field)) and not _empty(getattr(new, field)):
            update[field] = getattr(new, field)
    update["sources"] = sorted(set(base.sources) | set(new.sources))
    preferred, other = (new, base) if stronger else (base, new)
    update["themes"] = _merge_themes(preferred.themes, other.themes)
    update["origin"] = max(base.origin, new.origin)
    update["last_seen"] = max((d for d in (base.last_seen, new.last_seen) if d), default=None)
    update["notion_page_id"] = base.notion_page_id or new.notion_page_id
    if new.verified_at and (base.verified_at is None or new.verified_at >= base.verified_at):
        update["verification"] = new.verification
        update["verified_at"] = new.verified_at
    return base.model_copy(update=update)


def _needs_verification(ev: HackathonEvent, today: date, after_days: int) -> bool:
    return ev.verified_at is None or (today - ev.verified_at).days >= after_days


def _verify_priority(ev: HackathonEvent) -> tuple[bool, date, str]:
    """New events first, then the soonest deadline; URL breaks ties deterministically."""
    return ev.notion_page_id is not None, ev.closing_date() or date.max, ev.canonical_url


def dedupe(state: GraphState, deps: Deps) -> dict[str, Any]:
    today = deps.today
    existing = state.get("existing", {})

    seen: dict[str, HackathonEvent] = {}
    for candidate in state.get("candidates", []):
        candidate = candidate.model_copy(update={"last_seen": today})
        url = candidate.canonical_url
        seen[url] = merge(seen[url], candidate) if url in seen else candidate

    pool = {url: merge(existing[url], ev) if url in existing else ev for url, ev in seen.items()}
    for url, row in existing.items():  # active rows not seen today still get re-verified / archived
        if url not in pool and row.lifecycle == Lifecycle.ACTIVE:
            pool[url] = row

    to_verify: list[HackathonEvent] = []
    passthrough: list[HackathonEvent] = []
    for ev in pool.values():
        if ev.is_expired(today) or not _needs_verification(
            ev, today, deps.settings.reverify_after_days
        ):
            passthrough.append(ev)
        else:
            to_verify.append(ev)

    to_verify.sort(key=_verify_priority)
    passthrough.sort(key=lambda e: e.canonical_url)
    cap = deps.settings.max_verify
    return {"to_verify": to_verify[:cap], "passthrough": passthrough + to_verify[cap:]}
