"""Golden scenarios: each test is one design rule, run end to end through the whole graph."""

from datetime import date, timedelta

from hackathon_sniper.graph import run
from hackathon_sniper.http import Page
from hackathon_sniper.llm.base import LLMBudget
from hackathon_sniper.models import HackathonEvent, Lifecycle, Origin, Verification
from hackathon_sniper.nodes.report import exit_code
from hackathon_sniper.nodes.verify import PageFacts
from hackathon_sniper.nodes.web_search import ExtractedEvent, Extraction
from hackathon_sniper.notion.mapping import event_to_properties, page_to_event
from hackathon_sniper.search import SearchResult
from tests.factories import TODAY, event, llm_handlers, make_deps
from tests.fakes import FakeFetcher, FakeLLM, FakeNotion, FakeSearch


def rows(notion: FakeNotion) -> dict[str, HackathonEvent]:
    events = (page_to_event(p) for p in notion.query_all("hackathons"))
    return {e.canonical_url: e for e in events if e}


def test_same_event_from_two_sources_becomes_one_row() -> None:
    devpost = event(
        url="https://a.devpost.com",
        name="A",
        origin=Origin.SOURCE,
        sources=["devpost"],
        registration_deadline=TODAY + timedelta(days=10),
    )
    tracked = "https://a.devpost.com/?utm_source=x"
    search = FakeSearch(
        {"ai hackathon": [SearchResult(title="A", url=tracked, snippet="A hackathon")]}
    )
    llm = FakeLLM(
        llm_handlers(
            {
                Extraction: lambda u: Extraction(
                    events=[ExtractedEvent(is_hackathon=True, name="A", url=tracked)]
                )
            }
        )
    )
    deps = make_deps(llm=llm, search=search, sources={"devpost": lambda: [devpost]})

    final = run(deps)

    notion: FakeNotion = deps.notion  # type: ignore[assignment]
    assert list(rows(notion)) == ["https://a.devpost.com"]
    assert rows(notion)["https://a.devpost.com"].sources == ["devpost", "web"]
    assert exit_code(final, deps) == 0


def test_source_outage_does_not_archive_unseen_rows() -> None:
    notion = FakeNotion()
    kept = event(
        url="https://kept.dev",
        name="Kept",
        registration_deadline=TODAY + timedelta(days=20),
        verified_at=TODAY,
        verification=Verification.VERIFIED,
        origin=Origin.PAGE,
        fit_score=80,
    )
    notion.add_page("hackathons", event_to_properties(kept))

    def down() -> list[HackathonEvent]:
        raise RuntimeError("503")

    deps = make_deps(llm=FakeLLM(llm_handlers()), notion=notion, sources={"devpost": down})
    final = run(deps)

    assert rows(notion)["https://kept.dev"].lifecycle == Lifecycle.ACTIVE
    assert final["source_failures"] == ["devpost"]
    assert exit_code(final, deps) == 1  # devpost + web = 2 sources, half failed


def test_hallucinated_deadline_is_dropped() -> None:
    url = "https://c.devpost.com"
    llm = FakeLLM(
        llm_handlers(
            {
                PageFacts: lambda u: PageFacts(
                    registration_deadline=date(2026, 12, 1),
                    deadline_quote="Deadline: December 1, 2026",
                )
            }
        )
    )
    fetcher = FakeFetcher({url: Page(url=url, text="Welcome to C! Build anything. Online.")})
    c = event(url=url, name="C", origin=Origin.SOURCE, sources=["devpost"])
    deps = make_deps(llm=llm, fetcher=fetcher, sources={"devpost": lambda: [c]})

    run(deps)

    row = rows(deps.notion)[url]  # type: ignore[arg-type]
    assert row.registration_deadline is None
    assert row.verification == Verification.PARTIAL


def test_budget_exhaustion_degrades_gracefully() -> None:
    d = event(
        url="https://d.dev",
        name="D",
        origin=Origin.SOURCE,
        sources=["devpost"],
        registration_deadline=TODAY + timedelta(days=5),
    )
    llm = FakeLLM(llm_handlers(), budget=LLMBudget(1))  # only the Query Planner gets a call
    fetcher = FakeFetcher({"https://d.dev": Page(url="https://d.dev", text="D hackathon")})
    deps = make_deps(llm=llm, fetcher=fetcher, sources={"devpost": lambda: [d]})

    final = run(deps)

    row = rows(deps.notion)["https://d.dev"]  # type: ignore[arg-type]
    assert row.verification == Verification.UNVERIFIED and row.fit_score is None
    assert any("budget" in e for e in final["errors"])
    assert exit_code(final, deps) == 0


def test_user_columns_survive_upsert() -> None:
    notion = FakeNotion()
    e = event(
        url="https://e.dev",
        name="E",
        registration_deadline=TODAY + timedelta(days=9),
        verified_at=TODAY - timedelta(days=30),
    )
    pid = notion.add_page(
        "hackathons", event_to_properties(e) | {"Interested ⭐": {"checkbox": True}}
    )
    text = "E. Registration closes November 4, 2026. Prize pool $2,000."
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
    fetcher = FakeFetcher({"https://e.dev": Page(url="https://e.dev", text=text)})
    deps = make_deps(llm=llm, notion=notion, fetcher=fetcher)

    run(deps)

    page = notion.get_page(pid)
    assert page["properties"]["Interested ⭐"] == {"checkbox": True}
    assert page_to_event(page).verification == Verification.VERIFIED  # type: ignore[union-attr]


def test_expired_rows_are_archived() -> None:
    notion = FakeNotion()
    pid = notion.add_page(
        "hackathons",
        event_to_properties(
            event(url="https://f.dev", name="F", registration_deadline=TODAY - timedelta(days=1))
        ),
    )
    final = run(make_deps(llm=FakeLLM(llm_handlers()), notion=notion))
    assert page_to_event(notion.get_page(pid)).lifecycle == Lifecycle.EXPIRED  # type: ignore[union-attr]
    assert final["sync_result"].archived == 1


def test_dry_run_never_writes() -> None:
    notion = FakeNotion()
    g = event(url="https://g.dev", name="G", sources=["devpost"])
    final = run(
        make_deps(
            llm=FakeLLM(llm_handlers()),
            notion=notion,
            sources={"devpost": lambda: [g]},
            dry_run=True,
        )
    )
    assert notion.writes == [] and final["sync_result"].created == 1
