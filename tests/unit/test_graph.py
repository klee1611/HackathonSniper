import threading
import time
from datetime import timedelta

import pytest

from hackathon_sniper.graph import build_graph, route_verify, run
from hackathon_sniper.http import Page
from hackathon_sniper.models import HackathonEvent, Origin
from hackathon_sniper.nodes.report import exit_code
from hackathon_sniper.notion.client import NotionError
from hackathon_sniper.state import GraphState, SyncResult
from tests.factories import TODAY, event, llm_handlers, make_deps
from tests.fakes import FakeFetcher, FakeLLM, FakeNotion, FakeSearch


def test_graph_has_expected_nodes() -> None:
    graph = build_graph(make_deps(sources={"devpost": lambda: [], "mlh": lambda: []}))
    assert {
        "load_context",
        "plan_queries",
        "source_devpost",
        "source_mlh",
        "web_search",
        "dedupe",
        "verify",
        "rank",
        "sync_notion",
        "report",
    } <= set(graph.get_graph().nodes)


def test_route_verify_fans_out_one_send_per_event() -> None:
    a, b = event(url="https://a.dev"), event(url="https://b.dev")
    sends = route_verify({"to_verify": [a, b]})
    assert isinstance(sends, list)
    assert [(s.node, s.arg) for s in sends] == [("verify", {"event": a}), ("verify", {"event": b})]


def test_route_verify_skips_to_rank_when_nothing_to_verify() -> None:
    assert route_verify({}) == "rank"
    assert route_verify({"to_verify": []}) == "rank"


def test_empty_run_completes_with_zero_counts_and_a_report() -> None:
    summaries: list[str] = []
    deps = make_deps(llm=FakeLLM(llm_handlers()))
    deps.summary_writer = summaries.append

    final = run(deps)

    assert final["sync_result"] == SyncResult()
    assert final["ranked"] == []
    assert len(summaries) == 1 and "0 created" in summaries[0]
    assert exit_code(final, deps) == 0


def test_failing_source_and_failing_web_search_are_both_recorded() -> None:
    def down() -> list[HackathonEvent]:
        raise RuntimeError("503")

    summaries: list[str] = []
    ok = event(url="https://ok.dev", name="OK", sources=["mlh"], origin=Origin.SOURCE)
    deps = make_deps(
        llm=FakeLLM(llm_handlers()),
        search=FakeSearch({}, fail=True),
        sources={"devpost": down, "mlh": lambda: [ok]},
    )
    deps.summary_writer = summaries.append

    final = run(deps)

    assert sorted(final["source_failures"]) == ["devpost", "web"]
    assert any(e.startswith("source devpost:") for e in final["errors"])
    assert any(e.startswith("search ") for e in final["errors"])
    assert final["sync_result"].created == 1  # the healthy source still syncs
    assert len(summaries) == 1
    assert exit_code(final, deps) == 1  # 2 of 3 sources down


class CountingFetcher(FakeFetcher):
    """Records the peak number of fetches in flight at once."""

    def __init__(self, pages: dict[str, Page]) -> None:
        super().__init__(pages)
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def fetch(self, url: str) -> Page | None:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.05)
            return super().fetch(url)
        finally:
            with self._lock:
                self.active -= 1


def test_verify_fan_out_respects_max_concurrency() -> None:
    events = [
        event(
            url=f"https://e{i}.dev",
            name=f"E{i}",
            sources=["devpost"],
            registration_deadline=TODAY + timedelta(days=10 + i),
        )
        for i in range(6)
    ]
    fetcher = CountingFetcher({})
    deps = make_deps(
        llm=FakeLLM(llm_handlers()), fetcher=fetcher, sources={"devpost": lambda: events}
    )

    final = run(deps)

    assert sorted(fetcher.calls) == sorted(e.canonical_url for e in events)
    assert len(final["verified"]) == 6
    # Parallel (more than one at a time) but never above the configured cap.
    assert fetcher.peak == deps.settings.llm_concurrency


def test_load_context_failure_aborts_run_without_writes() -> None:
    notion = FakeNotion()
    calls: list[str] = []

    def devpost() -> list[HackathonEvent]:
        calls.append("devpost")
        return [event(url="https://x.dev", name="X", sources=["devpost"])]

    deps = make_deps(llm=FakeLLM(llm_handlers()), notion=notion, sources={"devpost": devpost})
    notion.databases.discard(deps.settings.notion_participations_db_id)

    with pytest.raises(NotionError):
        run(deps)

    assert notion.writes == []
    assert calls == []  # nothing downstream of load_context ran


def test_run_returns_full_state() -> None:
    deps = make_deps(llm=FakeLLM(llm_handlers()))
    final: GraphState = run(deps)
    assert final["queries"] == ["ai hackathon"]
    assert "profile" in final and "existing" in final
