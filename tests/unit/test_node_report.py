import re
from datetime import date

import pytest

from hackathon_sniper.models import HackathonEvent
from hackathon_sniper.nodes.report import MAX_ERRORS_LISTED, exit_code, render_report, report
from hackathon_sniper.state import GraphState, SyncResult
from tests.factories import event, make_deps


def test_report_summarizes_run() -> None:
    written: list[str] = []
    deps = make_deps()
    deps.summary_writer = written.append
    state: GraphState = {
        "queries": ["q"],
        "candidates": [
            event(sources=["devpost"]),
            event(url="https://b.dev", sources=["devpost", "web"]),
        ],
        "verified": [],
        "ranked": [event(url="https://b.dev", name="B", fit_score=90, fit_reason="Great | fit")],
        "sync_result": SyncResult(created=2),
        "errors": ["source mlh: boom"],
    }
    report(state, deps)
    md = written[0]
    assert "Candidates by source: devpost=2, web=1" in md
    assert "2 created" in md
    assert "| 90 | [B](https://b.dev) | Great \\| fit |" in md
    assert "source mlh: boom" in md


def test_report_shows_skipped_and_not_yet_scored() -> None:
    deps = make_deps()
    state: GraphState = {
        "ranked": [
            event(url="https://a.dev", fit_score=80),
            event(url="https://b.dev"),  # unscored, open
            event(url="https://c.dev", registration_deadline=date(2026, 11, 30)),  # unscored
            event(url="https://d.dev", registration_deadline=date(2026, 1, 1)),  # expired
            event(url="https://e.dev", fit_score=0, fit_reason="Filtered: Team required"),
        ],
        "sync_result": SyncResult(created=1, skipped=4, failed=1),
    }
    md = render_report(state, deps)
    assert "Not yet scored: 2" in md
    assert "1 created, 0 updated, 0 archived, 0 unchanged, 4 skipped, 1 failed" in md
    assert "https://e.dev" not in md  # zero / filtered scores aren't "top matches"
    assert "Mode: live" in md


def test_report_omits_empty_sections_and_shows_dry_run() -> None:
    md = render_report({}, make_deps(dry_run=True))
    assert "Mode: dry run" in md
    assert "Candidates by source: none" in md
    assert "Top matches" not in md
    assert "Problems" not in md


def _top_row(md: str) -> str:
    rows = [line for line in md.splitlines() if line.startswith("| 9")]
    assert len(rows) == 1
    return rows[0]


def test_report_escapes_table_breaking_names_and_reasons() -> None:
    ev = event(name="A | B\nC\\", fit_score=95, fit_reason="line1\nline2 | x")
    row = _top_row(render_report({"ranked": [ev]}, make_deps()))
    assert row == "| 95 | [A \\| B C\\\\](https://a.devpost.com) | line1 line2 \\| x |"
    assert len(re.findall(r"(?<!\\)\|", row)) == 4  # unescaped pipes: exactly three cells


def test_report_cannot_forge_links_from_names() -> None:
    ev = event(name="Win](javascript:alert(1)) [x", fit_score=95)
    row = _top_row(render_report({"ranked": [ev]}, make_deps()))
    assert "](javascript" not in row
    assert "Win\\]\\(javascript:alert\\(1\\)\\) \\[x" in row
    assert row.count("](") == 1


def test_report_escapes_html_in_untrusted_text() -> None:
    ev = event(name="<img src=x onerror=alert(1)>", fit_score=95, fit_reason="a <b> & <script>")
    md = render_report({"ranked": [ev], "errors": ["sync x: <svg onload=alert(1)>"]}, make_deps())
    assert "<" not in md and ">" not in md
    assert "&lt;img src=x onerror=alert\\(1\\)&gt;" in md
    assert "a &lt;b&gt; &amp; &lt;script&gt;" in md
    assert "- sync x: &lt;svg onload=alert\\(1\\)&gt;" in md


def test_report_links_only_http_urls() -> None:
    # The model validator forces https; build an event as if it had slipped past.
    bad = HackathonEvent.model_construct(
        canonical_url="javascript:alert(1)", name="Evil", fit_score=95, sources=[]
    )
    row = _top_row(render_report({"ranked": [bad]}, make_deps()))
    assert row == "| 95 | Evil |  |"


def test_report_percent_encodes_link_breaking_url_chars() -> None:
    ev = HackathonEvent.model_construct(
        canonical_url="https://a.dev/x) y|<z>", name="A", fit_score=95, sources=[]
    )
    row = _top_row(render_report({"ranked": [ev]}, make_deps()))
    assert "[A](https://a.dev/x%29%20y%7C%3Cz%3E) |" in row


def test_report_caps_and_escapes_error_list() -> None:
    errors = [f"err {i} | [x](y)\nnext" for i in range(MAX_ERRORS_LISTED + 7)]
    md = render_report({"errors": errors}, make_deps())
    listed = [line for line in md.splitlines() if line.startswith("- err ")]
    assert len(listed) == MAX_ERRORS_LISTED
    assert listed[0] == "- err 0 \\| \\[x\\]\\(y\\) next"
    assert md.endswith("- … and 7 more")


def test_report_neutralises_mentions_and_issue_references() -> None:
    ev = event(name="@octocat #123", fit_score=95, fit_reason="ping @team see #4 and org/repo#5")
    md = render_report({"ranked": [ev], "errors": ["sync @admin: see #99"]}, make_deps())
    assert re.search(r"@\w", md) is None
    assert re.search(r"#\w", md) is None
    assert "@\u2060octocat" in md


def test_report_percent_encodes_ampersand_in_link_target() -> None:
    ev = HackathonEvent.model_construct(
        canonical_url="https://a.dev&#64;evil.com", name="A", fit_score=95, sources=[]
    )
    row = _top_row(render_report({"ranked": [ev]}, make_deps()))
    assert "[A](https://a.dev%26#64;evil.com) |" in row
    assert "&" not in row


def test_exit_code_fails_on_many_sync_errors_or_half_the_sources_down() -> None:
    deps = make_deps(sources={"devpost": lambda: [], "mlh": lambda: []})  # + web = 3 sources
    assert exit_code({}, deps) == 0
    assert exit_code({"sync_result": SyncResult()}, deps) == 0
    assert exit_code({"sync_result": SyncResult(failed=1)}, deps) == 0  # one bad row on a quiet day
    assert exit_code({"source_failures": ["mlh"]}, deps) == 0
    assert exit_code({"source_failures": ["mlh", "web"]}, deps) == 1
    assert exit_code({"source_failures": ["mlh", "mlh"]}, deps) == 0  # deduplicated


@pytest.mark.parametrize(
    ("sync", "expected"),
    [
        (SyncResult(created=9, failed=1), 0),  # one bad row: listed, not red
        (SyncResult(created=1, failed=2), 0),  # 2 of 3: below the 3-failure floor
        (SyncResult(failed=2), 0),  # 2 of 2: still below the 3-failure floor
        (SyncResult(failed=3), 1),  # 3 of 3
        (SyncResult(created=7, failed=3), 1),  # 3 of 10 = 30%
        (SyncResult(created=12, failed=3), 1),  # 3 of 15 = exactly 20%
        (SyncResult(created=13, failed=3), 0),  # 3 of 16 < 20%
        (SyncResult(created=17, failed=3), 0),  # 3 of 20 = 15%
        (SyncResult(updated=3, archived=4, failed=3), 1),  # updates/archives are attempts
        # unchanged / skipped rows aren't attempted writes
        (SyncResult(created=1, unchanged=500, skipped=50, failed=3), 1),
        (SyncResult(unchanged=10, skipped=2), 0),
    ],
)
def test_exit_code_sync_failure_thresholds(sync: SyncResult, expected: int) -> None:
    deps = make_deps(sources={"devpost": lambda: [], "mlh": lambda: []})
    assert exit_code({"sync_result": sync}, deps) == expected


def test_report_scrubs_configured_secrets_before_escaping() -> None:
    key = "gsk_planted_secret_key"
    deps = make_deps(groq_api_key=key, notion_token="ntn_planted_token")
    ev = event(name=f"leak {key}", fit_score=80, fit_reason="ntn_planted_token")
    state: GraphState = {
        "ranked": [ev],
        "errors": [f"plan_queries fell back: Error code: 401 - Bearer {key}"],
    }
    md = render_report(state, deps)
    assert "Bearer REDACTED" in md
    assert "leak REDACTED" in md
    assert key not in md and "gsk\\_planted" not in md
    assert "ntn_planted_token" not in md and "ntn\\_planted" not in md
