import threading
from datetime import date, timedelta

import pytest

from hackathon_sniper.graph import build_graph, route_verify, run
from hackathon_sniper.http import Page
from hackathon_sniper.models import HackathonEvent, Origin, Verification
from hackathon_sniper.nodes.rank import Ranking
from hackathon_sniper.nodes.report import exit_code
from hackathon_sniper.nodes.verify import PageFacts
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
    """Records the peak number of fetches in flight at once.

    The first `cap` calls meet at a Barrier, so reaching `cap` parallel fetches is proven
    deterministically (if they ran one at a time the barrier would time out and break).
    The barrier group then stays in flight until a further call starts or HOLD seconds pass:
    with an executor wider than `cap`, that extra call would start at once while the group
    is held and be counted (peak > cap). Under a correct cap it can't start until one of
    the group leaves, so the hold is the only wait and is paid once.
    """

    HOLD = 0.05

    def __init__(self, pages: dict[str, Page], cap: int) -> None:
        super().__init__(pages)
        self.cap = cap
        self.active = 0
        self.peak = 0
        self.started = 0
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(cap, timeout=5)
        self._extra = threading.Event()

    def fetch(self, url: str) -> Page | None:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.started += 1
            in_group = self.started <= self.cap
        try:
            if in_group:
                self._barrier.wait()  # BrokenBarrierError if the cap-wide group never forms
                self._extra.wait(self.HOLD)
            else:
                self._extra.set()
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
    deps = make_deps(llm=FakeLLM(llm_handlers()), sources={"devpost": lambda: events})
    fetcher = CountingFetcher({}, cap=deps.settings.llm_concurrency)
    deps.fetcher = fetcher

    final = run(deps)

    assert sorted(fetcher.calls) == sorted(e.canonical_url for e in events)
    assert len(final["verified"]) == 6
    # Every fetch got past the barrier: none failed with BrokenBarrierError.
    assert all("BrokenBarrierError" not in e for e in final["errors"])
    assert fetcher.peak == deps.settings.llm_concurrency


class RaisingFetcher(FakeFetcher):
    def __init__(self, pages: dict[str, Page], bad: str) -> None:
        super().__init__(pages)
        self.bad = bad

    def fetch(self, url: str) -> Page | None:
        if url == self.bad:
            raise RuntimeError("boom " + "x" * 400)
        return super().fetch(url)


def test_unexpected_verify_error_is_isolated_to_its_event() -> None:
    text = "Registration closes November 4, 2026. Prize pool $2,000."
    urls = ["https://v1.dev", "https://v2.dev", "https://v3.dev"]
    events = [event(url=u, name=u[8:10], sources=["devpost"]) for u in urls]
    llm = FakeLLM(
        llm_handlers(
            {
                PageFacts: lambda u: PageFacts(
                    registration_deadline=date(2026, 11, 4),
                    deadline_quote="Registration closes November 4, 2026",
                    prize_usd=2000,
                    prize_quote="Prize pool $2,000",
                )
            }
        )
    )
    fetcher = RaisingFetcher({u: Page(url=u, text=text) for u in urls}, bad=urls[1])
    summaries: list[str] = []
    deps = make_deps(llm=llm, fetcher=fetcher, sources={"devpost": lambda: events})
    deps.summary_writer = summaries.append

    final = run(deps)

    by_url = {e.canonical_url: e for e in final["verified"]}
    assert by_url[urls[0]].verification == Verification.VERIFIED
    assert by_url[urls[2]].verification == Verification.VERIFIED
    assert by_url[urls[1]] == events[1].model_copy(update={"last_seen": TODAY})  # unchanged
    errors = [e for e in final["errors"] if e.startswith(f"verify {urls[1]}: RuntimeError: boom")]
    assert len(errors) == 1 and len(errors[0]) <= 300
    assert final["sync_result"].created == 3
    assert len(summaries) == 1


def test_unexpected_rank_error_still_syncs_unscored_events() -> None:
    def broken(user: str) -> Ranking:
        raise RuntimeError("ranker exploded")

    events = [event(url=f"https://r{i}.dev", name=f"R{i}", sources=["devpost"]) for i in range(2)]
    summaries: list[str] = []
    deps = make_deps(
        llm=FakeLLM(llm_handlers({Ranking: broken})), sources={"devpost": lambda: events}
    )
    deps.summary_writer = summaries.append

    final = run(deps)

    assert "rank: RuntimeError: ranker exploded" in final["errors"]
    assert sorted(e.canonical_url for e in final["ranked"]) == ["https://r0.dev", "https://r1.dev"]
    assert all(e.fit_score is None for e in final["ranked"])
    assert final["sync_result"].created == 2
    assert len(summaries) == 1


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
