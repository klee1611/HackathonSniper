import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import evals.extraction_eval as extraction_eval
from evals.extraction_eval import EVAL_BUDGET, run_eval
from hackathon_sniper.llm.base import LLMBudget, LLMError
from hackathon_sniper.models import Format, Solo
from hackathon_sniper.nodes.verify import PageFacts
from tests.fakes import FakeLLM

ALPHA_HTML = """<html><head><script>var secret = 'not page text';</script></head>
<body><h1>Alpha Hack</h1><p>Register by <b>November 1, 2026</b>.</p>
<p>Prizes: $10,000 in total. Fully online. Solo hackers welcome.</p></body></html>"""
BETA_HTML = "<html><body><p>Applications close Dec 5, 2026. Teams of 2-4.</p></body></html>"


def _case(cases: Path, name: str, expected: dict[str, object], html: str | None) -> None:
    url = f"https://{name}.example.com"
    (cases / f"{name}.json").write_text(json.dumps({"url": url, "expected": expected}))
    if html is not None:
        (cases / f"{name}.html").write_text(html)


def _facts(user: str) -> PageFacts:
    if "alpha.example.com" in user:
        return PageFacts(
            registration_deadline=date(2026, 11, 1),
            deadline_quote="Register by November 1, 2026",
            prize_usd=10_000,
            prize_quote="Prizes: $10,000 in total",
            format=Format.ONLINE,
            solo_allowed=Solo.YES,
        )
    if "beta.example.com" in user:
        return PageFacts(
            registration_deadline=date(2026, 12, 5),
            deadline_quote="Applications close Dec 5, 2026",
            prize_usd=5_000,
            prize_quote="$5,000 grand prize",  # not on the page -> dropped
            format=Format.ONLINE,
            solo_allowed=Solo.NO,
        )
    raise LLMError("provider exploded")


@pytest.fixture
def cases(tmp_path: Path) -> Path:
    full = {"format": "Online", "solo_allowed": "Yes"}
    _case(
        tmp_path,
        "alpha",
        {"registration_deadline": "2026-11-01", "prize_usd": 10000, **full},
        ALPHA_HTML,
    )
    _case(
        tmp_path,
        "beta",
        {
            "registration_deadline": "2026-12-05",
            "prize_usd": 5000,
            "format": "In-person",
            "solo_allowed": "No",
        },
        BETA_HTML,
    )
    return tmp_path


def test_per_field_accuracy_and_mismatches(cases: Path) -> None:
    llm = FakeLLM({PageFacts: _facts}, budget=LLMBudget(EVAL_BUDGET))
    report = run_eval(cases, llm).splitlines()

    assert "mismatch beta.prize_usd: expected 5000, got None" in report
    assert "mismatch beta.format: expected 'In-person', got 'Online'" in report
    assert sum(line.startswith("mismatch") for line in report) == 2
    assert "registration_deadline    2/2 = 100%" in report
    assert "prize_usd                1/2 = 50%" in report
    assert "format                   1/2 = 50%" in report
    assert "solo_allowed             2/2 = 100%" in report
    assert f"LLM calls used: 2/{EVAL_BUDGET}" in report


def test_uses_production_prompt_on_fetcher_text(cases: Path) -> None:
    llm = FakeLLM({PageFacts: _facts})
    run_eval(cases, llm)
    alpha = next(user for _, user in llm.calls if "alpha" in user)
    assert "<page>" in alpha and "Register by November 1, 2026" in alpha
    assert "not page text" not in alpha  # html_to_text drops <script>


def test_page_text_capped_like_the_fetcher(tmp_path: Path) -> None:
    _case(tmp_path, "huge", {"format": "Online"}, "<p>" + "x" * 20_000 + "</p>")
    llm = FakeLLM({PageFacts: lambda _: PageFacts()})
    run_eval(tmp_path, llm)
    (_, user), *_ = llm.calls
    assert user.count("x") <= 5000 + 10


def test_case_without_html_is_skipped(cases: Path) -> None:
    _case(cases, "gamma", {"format": "Online"}, None)
    report = run_eval(cases, FakeLLM({PageFacts: _facts})).splitlines()
    assert "skip gamma: no gamma.html" in report
    assert "format                   1/2 = 50%" in report


def test_malformed_case_reports_error_and_continues(cases: Path) -> None:
    (cases / "bad.json").write_text("{not json")
    (cases / "bad.html").write_text("<p>hi</p>")
    _case(cases, "nourl", {"format": "Online"}, "<p>hi</p>")
    (cases / "nourl.json").write_text(json.dumps({"expected": {"format": "Online"}}))
    _case(cases, "oddfield", {"colour": "blue"}, "<p>hi</p>")
    llm = FakeLLM({PageFacts: _facts})
    report = run_eval(cases, llm).splitlines()

    assert any(line.startswith("error bad: invalid case:") for line in report)
    assert any(line.startswith("error nourl: invalid case:") for line in report)
    assert any(line.startswith("error oddfield: invalid case:") for line in report)
    assert "prize_usd                1/2 = 50%" in report  # the good cases still ran
    assert len(llm.calls) == 2


def test_llm_error_reports_and_continues(cases: Path) -> None:
    _case(cases, "crash", {"format": "Online"}, "<p>hi</p>")
    report = run_eval(cases, FakeLLM({PageFacts: _facts})).splitlines()
    assert "error crash: LLMError: provider exploded" in report
    assert "format                   1/2 = 50%" in report


def test_budget_exhaustion_stops_the_run(cases: Path) -> None:
    llm = FakeLLM({PageFacts: _facts}, budget=LLMBudget(1))
    report = run_eval(cases, llm).splitlines()
    assert any(line.startswith("error beta: BudgetExceeded") for line in report)
    assert "LLM calls used: 1/1" in report


def test_no_cases(tmp_path: Path) -> None:
    assert "no cases evaluated" in run_eval(tmp_path, FakeLLM())


def test_main_needs_only_groq_key(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(extraction_eval, "load_dotenv", lambda: None)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    built: dict[str, Any] = {}

    def fake_groq(api_key: str, model: str, budget: LLMBudget) -> FakeLLM:
        built.update(api_key=api_key, model=model, limit=budget.limit)
        return FakeLLM(budget=budget)

    monkeypatch.setattr(extraction_eval, "GroqLLM", fake_groq)
    monkeypatch.setattr(extraction_eval, "run_eval", lambda d, llm: "REPORT")
    extraction_eval.main()
    assert built == {"api_key": "gsk_test", "model": "llama-3.3-70b-versatile", "limit": 500}
    assert "REPORT" in capsys.readouterr().out


def test_main_without_key_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(extraction_eval, "load_dotenv", lambda: None)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="GROQ_API_KEY"):
        extraction_eval.main()


def test_decodes_declared_charset_like_the_fetcher(tmp_path: Path) -> None:
    _case(tmp_path, "cafe", {"format": "Online"}, None)
    html = '<html><head><meta charset="windows-1252"></head><body><p>Caf\u00e9 Hack</p></body>'
    (tmp_path / "cafe.html").write_bytes(html.encode("windows-1252"))
    llm = FakeLLM({PageFacts: lambda _: PageFacts()})
    run_eval(tmp_path, llm)
    (_, user), *_ = llm.calls
    assert "Caf\u00e9 Hack" in user
