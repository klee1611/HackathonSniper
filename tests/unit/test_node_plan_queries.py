import dataclasses
from datetime import date
from typing import Any

import pytest

from hackathon_sniper.llm.base import BudgetExceeded, LLMBudget, LLMError
from hackathon_sniper.models import Participation, Profile
from hackathon_sniper.nodes.plan_queries import (
    MAX_QUERY_CHARS,
    SYSTEM,
    QueryPlan,
    default_queries,
    plan_queries,
)
from hackathon_sniper.state import GraphState
from tests.factories import make_deps
from tests.fakes import FakeLLM

# make_deps pins today to 2026-10-26: late in the year, so next year is searched too.
LATE_YEAR_DEFAULTS = ["AI hackathon 2026", "online hackathon 2026 prizes", "AI hackathon 2027"]


def _llm(queries: list[str] | None) -> FakeLLM:
    return FakeLLM({QueryPlan: lambda u: QueryPlan(queries=queries)})


def _run(llm: FakeLLM, profile: Profile | None = None, **settings: Any) -> dict[str, Any]:
    state: GraphState = {"profile": profile or Profile(interests=["AI"]), "history": []}
    return plan_queries(state, make_deps(llm=llm, **settings))


def test_uses_llm_queries_deduped_and_capped() -> None:
    llm = FakeLLM(
        {
            QueryPlan: lambda u: QueryPlan(
                queries=["a", "a", " b ", "", *[f"q{i}" for i in range(20)]]
            )
        }
    )
    out = plan_queries(
        {"profile": Profile(interests=["AI"]), "history": []}, make_deps(llm=llm, max_queries=3)
    )
    assert out["queries"] == ["a", "b", "q0"]
    assert out["errors"] == []
    assert "Interests: AI" in llm.calls[0][1]


def test_falls_back_to_default_queries_on_llm_error() -> None:
    def down(_: str) -> QueryPlan:
        raise LLMError("down")

    out = plan_queries(
        {"profile": Profile(interests=["AI"]), "history": []},
        make_deps(llm=FakeLLM({QueryPlan: down})),
    )
    assert out["queries"] == LATE_YEAR_DEFAULTS
    assert out["errors"] and "down" in out["errors"][0]


def test_falls_back_when_budget_is_exhausted() -> None:
    budget = LLMBudget(0)
    llm = FakeLLM({QueryPlan: lambda u: QueryPlan(queries=["x"])}, budget=budget)
    out = _run(llm)
    assert out["queries"][0] == "AI hackathon 2026"
    assert out["errors"]
    assert llm.calls == []  # the budget refused before any call
    with pytest.raises(BudgetExceeded):
        budget.take()


def test_dedupe_is_case_insensitive_and_whitespace_normalised() -> None:
    out = _run(_llm(["AI hackathon 2026", "ai  hackathon\n2026", "AI HACKATHON 2026 ", "b"]))
    assert out["queries"] == ["AI hackathon 2026", "b"]


def test_queries_are_collapsed_to_one_line_and_length_capped() -> None:
    long = "word " * 200
    out = _run(_llm(["  multi\n\tline   query ", long, "\n \t "]))
    queries = out["queries"]
    assert queries[0] == "multi line query"
    assert len(queries) == 2
    assert len(queries[1]) <= MAX_QUERY_CHARS
    assert not queries[1].endswith(" ")


@pytest.mark.parametrize("raw", [None, [], ["", "   ", "\n"]])
def test_empty_llm_plan_falls_back_with_error(raw: list[str] | None) -> None:
    out = _run(_llm(raw))
    assert out["queries"] == LATE_YEAR_DEFAULTS
    assert out["errors"]


def test_query_plan_tolerates_missing_or_null_queries() -> None:
    assert QueryPlan.model_validate({}).queries == []
    assert QueryPlan.model_validate({"queries": None}).queries == []
    assert QueryPlan.model_validate_json('{"queries": null}').queries == []


def test_fallback_is_capped_at_max_queries() -> None:
    profile = Profile(interests=["AI", "web3", "health", "climate", "games", "robots"])
    out = _run(_llm(None), profile=profile, max_queries=2)
    assert out["queries"] == ["AI hackathon 2026", "web3 hackathon 2026"]


def test_prompt_includes_limit_date_history_and_upcoming_years() -> None:
    llm = _llm(["x"])
    history = [Participation(hackathon="HackMIT", project="Bot")]
    plan_queries(
        {"profile": Profile(interests=["AI"]), "history": history},
        make_deps(llm=llm, max_queries=4),
    )
    user = llm.calls[0][1]
    assert "2026-10-26" in user
    assert "at most 4 queries" in user
    assert "HackMIT" in user
    assert "upcoming" in user
    assert "2027" in user
    assert "upcoming" in SYSTEM


def test_early_in_year_does_not_search_next_year() -> None:
    llm = _llm(None)
    deps = dataclasses.replace(make_deps(llm=llm), today=date(2026, 3, 1))
    out = plan_queries({"profile": Profile(interests=["AI"]), "history": []}, deps)
    assert out["queries"] == ["AI hackathon 2026", "online hackathon 2026 prizes"]
    assert "2027" not in llm.calls[0][1]


def test_default_queries_basic() -> None:
    assert default_queries(Profile(interests=["AI"]), 2026) == [
        "AI hackathon 2026",
        "online hackathon 2026 prizes",
    ]


def test_default_queries_next_year() -> None:
    assert default_queries(Profile(interests=["AI", "web"]), 2026, include_next_year=True) == [
        "AI hackathon 2026",
        "web hackathon 2026",
        "online hackathon 2026 prizes",
        "AI hackathon 2027",
    ]


def test_default_queries_handles_blank_interests() -> None:
    assert default_queries(Profile(interests=["", "  ", "\n"]), 2026) == [
        "AI hackathon 2026",
        "developer tools hackathon 2026",
        "online hackathon 2026 prizes",
    ]
    assert default_queries(Profile(), 2026)[0] == "AI hackathon 2026"


def test_default_queries_dedupes_normalises_and_caps_topics() -> None:
    profile = Profile(interests=["AI", " ai ", "Web\n3", "web 3", "a", "b", "c", "d", "e"])
    queries = default_queries(profile, 2026)
    assert queries == [
        "AI hackathon 2026",
        "Web 3 hackathon 2026",
        "a hackathon 2026",
        "b hackathon 2026",
        "c hackathon 2026",
        "online hackathon 2026 prizes",
    ]


def test_default_queries_caps_long_interest() -> None:
    queries = default_queries(Profile(interests=["x" * 1000]), 2026)
    assert all(len(q) <= MAX_QUERY_CHARS for q in queries)


def test_system_prompt_says_listing_sources_are_already_covered() -> None:
    for source in ("Devpost", "MLH", "Devfolio"):
        assert source in SYSTEM
    assert "already covered" in SYSTEM
