"""Run summary (Markdown for the GitHub Actions job summary) and the run's exit code.

The job summary of a public repo is public, and nearly everything shown here (event names,
URLs, fit reasons, error strings) comes from scraped pages or LLM output. Every interpolated
value goes through `_md` / `_link_url` so it can't break the table, forge a link, or inject
HTML.
"""

from collections import Counter
from typing import Any
from urllib.parse import quote, urlsplit

from hackathon_sniper.deps import Deps
from hackathon_sniper.state import GraphState, SyncResult
from hackathon_sniper.text import MAX_NAME_CHARS, MAX_URL_CHARS, one_line

TOP_MATCHES = 5
MAX_ERRORS_LISTED = 50
MAX_REASON_CHARS = 300
MAX_ERROR_CHARS = 500

# Exit-code thresholds for Notion sync failures (see exit_code).
MIN_SYNC_FAILURES = 3
SYNC_FAILURE_PERCENT = 20

# CommonMark lets any ASCII punctuation be backslash-escaped; these are the ones that can
# start emphasis, code, links, images or a table cell boundary.
_MD_SPECIAL = "\\`*_{}[]()#+!|~"
# Characters kept as-is in a link target; everything else (space, ( ) [ ] < > " ' | ` \ and
# non-ASCII) is percent-encoded so it can't end the link or the table cell.
_URL_SAFE = ":/?#@!$&*+,;=%~-._"


def _md(text: str, limit: int) -> str:
    """Untrusted text as inert inline Markdown: one line, capped, no HTML, no link syntax."""
    text = one_line(text, limit)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join("\\" + c if c in _MD_SPECIAL else c for c in text)


def _link_url(url: str) -> str | None:
    """A safe link target, or None if the URL isn't plain http(s)."""
    url = one_line(url, MAX_URL_CHARS)
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return quote(url, safe=_URL_SAFE)


def _event_cell(name: str, url: str) -> str:
    label = _md(name, MAX_NAME_CHARS) or "(unnamed)"
    target = _link_url(url)
    return f"[{label}]({target})" if target else label


def render_report(state: GraphState, deps: Deps) -> str:
    sync = state.get("sync_result") or SyncResult()
    by_source = Counter(src for ev in state.get("candidates", []) for src in ev.sources)
    ranked = state.get("ranked", [])
    unscored = sum(1 for e in ranked if e.fit_score is None and not e.is_expired(deps.today))
    mode = "dry run (no Notion writes)" if deps.settings.dry_run else "live"
    sources = ", ".join(f"{_md(k, 50)}={v}" for k, v in sorted(by_source.items()))
    lines = [
        f"## HackathonSniper run — {deps.today.isoformat()}",
        "",
        f"- Mode: {mode}",
        f"- Queries: {len(state.get('queries', []))}",
        "- Candidates by source: " + (sources or "none"),
        f"- Verified this run: {len(state.get('verified', []))}",
        f"- Not yet scored: {unscored}",
        f"- LLM calls: {deps.llm.budget.used}/{deps.llm.budget.limit}",
        f"- Notion: {sync.created} created, {sync.updated} updated, {sync.archived} archived, "
        f"{sync.unchanged} unchanged, {sync.skipped} skipped, {sync.failed} failed",
    ]
    top = [e for e in ranked if (e.fit_score or 0) > 0][:TOP_MATCHES]
    if top:
        lines += ["", "### Top matches", "", "| Score | Event | Why |", "|---|---|---|"]
        lines += [
            f"| {e.fit_score} | {_event_cell(e.name, e.canonical_url)} "
            f"| {_md(e.fit_reason or '', MAX_REASON_CHARS)} |"
            for e in top
        ]
    errors = state.get("errors", [])
    if errors:
        lines += ["", "### Problems", ""]
        lines += [f"- {_md(err, MAX_ERROR_CHARS)}" for err in errors[:MAX_ERRORS_LISTED]]
        if len(errors) > MAX_ERRORS_LISTED:
            lines.append(f"- … and {len(errors) - MAX_ERRORS_LISTED} more")
    return "\n".join(lines)


def report(state: GraphState, deps: Deps) -> dict[str, Any]:
    deps.summary_writer(render_report(state, deps))
    return {}


def _sync_failed(sync: SyncResult) -> bool:
    """Persistent per-row failures are listed but only turn the run red when they're many:
    ≥ MIN_SYNC_FAILURES and ≥ SYNC_FAILURE_PERCENT% of attempted writes, or every write failed."""
    attempted = sync.created + sync.updated + sync.archived + sync.failed
    if sync.failed == 0:
        return False
    if sync.failed == attempted:
        return True
    many = sync.failed >= MIN_SYNC_FAILURES
    return many and sync.failed * 100 >= SYNC_FAILURE_PERCENT * attempted


def exit_code(state: GraphState, deps: Deps) -> int:
    """Red run (→ GitHub email) only for real problems: Notion sync broadly failing, or
    at least half the sources down."""
    sync = state.get("sync_result") or SyncResult()
    total_sources = len(deps.sources) + 1  # + web search
    failed_sources = len(set(state.get("source_failures", [])))
    return 1 if _sync_failed(sync) or failed_sources * 2 >= total_sources else 0
