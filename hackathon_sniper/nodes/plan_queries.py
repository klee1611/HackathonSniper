from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, Field, field_validator

from hackathon_sniper.deps import Deps
from hackathon_sniper.llm.base import LLMError
from hackathon_sniper.models import Profile
from hackathon_sniper.nodes._context import history_summary, profile_summary
from hackathon_sniper.state import GraphState

# Every query is a paid/rate-limited search call, so the list is cleaned hard before use.
MAX_QUERY_CHARS = 200
MAX_TOPIC_CHARS = 60
MAX_DEFAULT_TOPICS = 5
# From October on, many announced events fall in the next calendar year.
NEXT_YEAR_FROM_MONTH = 10

SYSTEM = (
    "You plan web searches that discover upcoming hackathons for one developer. "
    "Write specific, diverse queries (themes, formats, regions, sponsors) that surface the pages "
    "of individual events, not news articles or listicles. Include the year. "
    "Each query is a short search-engine query on one line. "
    "Devpost, MLH and Devfolio are already covered; target other sources such as company, "
    "university, community or lu.ma event pages."
)


class QueryPlan(BaseModel):
    """Web search queries to run."""

    queries: list[str] = Field(default_factory=list)

    @field_validator("queries", mode="before")
    @classmethod
    def _none_is_empty(cls, v: Any) -> Any:
        # A null/missing list means "no plan" (we fall back), not a validation failure.
        return [] if v is None else v


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip()


def _clean(queries: Iterable[str], limit: int) -> list[str]:
    """One-line, length-capped, non-empty, case-insensitively unique; at most `limit`."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in queries:
        q = _one_line(raw, MAX_QUERY_CHARS)
        key = q.casefold()
        if not q or key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= limit:
            break
    return out


def default_queries(profile: Profile, year: int, *, include_next_year: bool = False) -> list[str]:
    topics = _clean(
        (_one_line(t, MAX_TOPIC_CHARS) for t in profile.interests), MAX_DEFAULT_TOPICS
    ) or ["AI", "developer tools"]
    queries = [f"{t} hackathon {year}" for t in topics] + [f"online hackathon {year} prizes"]
    if include_next_year:
        queries.append(f"{topics[0]} hackathon {year + 1}")
    return _clean(queries, len(queries))


def plan_queries(state: GraphState, deps: Deps) -> dict[str, Any]:
    profile = state["profile"]
    limit = deps.settings.max_queries
    today = deps.today
    next_year = today.month >= NEXT_YEAR_FROM_MONTH
    years = f"{today.year} or {today.year + 1}" if next_year else str(today.year)
    user = (
        f"Today: {today.isoformat()}\n\nProfile:\n{profile_summary(profile)}\n\n"
        f"Past participation:\n{history_summary(state.get('history', []))}\n\n"
        f"Find upcoming hackathons taking place in {years}. "
        f"Return at most {limit} queries."
    )
    errors: list[str] = []
    queries: list[str] = []
    try:
        plan = deps.llm.structured(QueryPlan, SYSTEM, user)
        queries = _clean(plan.queries, limit)
        if not queries:
            errors.append("plan_queries: LLM returned no usable queries; using default queries")
    except LLMError as e:
        errors.append(f"plan_queries: {e}; using default queries")
    if not queries:
        queries = _clean(default_queries(profile, today.year, include_next_year=next_year), limit)
    return {"queries": queries, "errors": errors}
