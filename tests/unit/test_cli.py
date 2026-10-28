import dataclasses
import logging
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

import hackathon_sniper.__main__ as cli
import hackathon_sniper.deps as deps_module
from hackathon_sniper.config import Settings
from hackathon_sniper.deps import Deps
from hackathon_sniper.http import PageFetcher
from hackathon_sniper.llm.groq import GroqLLM
from hackathon_sniper.notion.client import NotionError, NotionGateway
from hackathon_sniper.search import BraveSearch, SerpApiSearch
from hackathon_sniper.sources.devfolio import DevfolioSource
from hackathon_sniper.sources.devpost import DevpostSource
from hackathon_sniper.sources.mlh import MLHSource
from tests.factories import llm_handlers, make_deps, make_settings
from tests.fakes import FakeFetcher, FakeLLM

ENV = {
    "GROQ_API_KEY": "g",
    "NOTION_TOKEN": "n",
    "NOTION_HACKATHONS_DB_ID": "hackathons",
    "NOTION_PARTICIPATIONS_DB_ID": "participations",
    "NOTION_PROFILE_PAGE_ID": "profile",
    "BRAVE_API_KEY": "b",
}
SECRET = "sk-serp-secret-987"


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Settings reads ./.env: run from an empty dir so a developer's real .env can't leak in."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)


_LOGGERS = (*cli.NOISY_LOGGERS, "httpx", "hackathon_sniper")


@pytest.fixture(autouse=True)
def _restore_logger_levels() -> Iterator[None]:
    """main() sets per-logger levels; don't let them leak into other tests."""
    saved = {name: logging.getLogger(name).level for name in _LOGGERS}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


def _set_env(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    for key, value in (ENV | extra).items():
        monkeypatch.setenv(key, value)


def test_main_applies_flags_runs_graph_and_returns_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_env(monkeypatch)
    seen: dict[str, Any] = {}

    def fake_build(settings: Settings, client: httpx.Client) -> Deps:
        seen["settings"] = settings
        return make_deps(
            llm=FakeLLM(llm_handlers()), dry_run=settings.dry_run, max_verify=settings.max_verify
        )

    monkeypatch.setattr(cli, "build_deps", fake_build)
    assert cli.main(["--dry-run", "--max-verify", "3"]) == 0
    assert seen["settings"].dry_run is True and seen["settings"].max_verify == 3


def test_help_works_without_any_env(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "usage: hackathon_sniper" in out and "--dry-run" in out and "--max-verify" in out


def test_missing_settings_exit_2_with_clear_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-very-secret")
    monkeypatch.setattr(cli, "build_deps", lambda *a: pytest.fail("must not build deps"))
    assert cli.main([]) == 2
    err = capsys.readouterr().err
    assert "Configuration error" in err
    assert "NOTION_TOKEN: Field required" in err
    assert "Traceback" not in err
    assert "gsk-very-secret" not in err


@pytest.mark.parametrize(
    ("argv", "extra", "expected"),
    [
        # field validation: the bad value itself must not be echoed
        (["--max-verify", "-99"], {}, "MAX_VERIFY: Input should be greater than or equal to 0"),
        # model validator: serpapi selected without its key
        ([], {"SEARCH_PROVIDER": "serpapi"}, "SERPAPI_API_KEY is required"),
    ],
)
def test_invalid_settings_never_echo_secrets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    extra: dict[str, str],
    expected: str,
) -> None:
    _set_env(monkeypatch, GROQ_API_KEY="gsk-x1", NOTION_TOKEN="ntn-x2", **extra)
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    text = captured.err + captured.out
    assert expected in text
    assert "Traceback" not in text
    assert "gsk-x1" not in text and "ntn-x2" not in text and "-99" not in text


def test_config_error_notes_job_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert cli.main([]) == 2
    assert "configuration error" in summary.read_text()


def test_unexpected_error_logs_concisely_notes_summary_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _set_env(monkeypatch)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(cli, "build_deps", lambda settings, client: make_deps())

    def boom(deps: Deps) -> Any:
        raise NotionError("POST /databases/x/query failed:\nHTTP 401")

    monkeypatch.setattr(cli, "run", boom)
    with caplog.at_level(logging.INFO):
        assert cli.main([]) == 1
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage() == (
        "run aborted: NotionError: POST /databases/x/query failed: HTTP 401"
    )
    assert errors[0].exc_info is None  # no traceback in public logs
    note = summary.read_text()
    assert "run aborted" in note and "NotionError" in note


def test_unwritable_summary_does_not_mask_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_env(monkeypatch)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "missing-dir" / "summary.md"))
    monkeypatch.setattr(cli, "build_deps", lambda settings, client: make_deps())

    def boom(deps: Deps) -> Any:
        raise RuntimeError("x")

    monkeypatch.setattr(cli, "run", boom)
    assert cli.main([]) == 1


@pytest.mark.parametrize("fail", [False, True])
def test_shared_client_is_closed(monkeypatch: pytest.MonkeyPatch, fail: bool) -> None:
    _set_env(monkeypatch)
    clients: list[httpx.Client] = []

    def fake_build(settings: Settings, client: httpx.Client) -> Deps:
        clients.append(client)
        return make_deps(llm=FakeLLM(llm_handlers()))

    def failing_run(deps: Deps) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "build_deps", fake_build)
    if fail:
        monkeypatch.setattr(cli, "run", failing_run)
    assert cli.main([]) == (1 if fail else 0)
    assert len(clients) == 1 and clients[0].is_closed


@respx.mock
def test_serpapi_key_never_reaches_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Real wiring with SerpApi (the one provider whose key is a query param); only the
    LLM, Notion, fetcher and listing sources are faked. httpx logs request URLs at INFO."""
    _set_env(monkeypatch, SEARCH_PROVIDER="serpapi", SERPAPI_API_KEY=SECRET)
    route = respx.get(url__startswith=SerpApiSearch.URL).mock(
        return_value=httpx.Response(200, json={"organic_results": []})
    )
    real_build = deps_module.build_deps

    def build(settings: Settings, client: httpx.Client) -> Deps:
        real = real_build(settings, client)
        faked = make_deps(llm=FakeLLM(llm_handlers()))
        return dataclasses.replace(
            real,
            llm=faked.llm,
            notion=faked.notion,
            fetcher=FakeFetcher({}),
            sources={},
            summary_writer=print,
        )

    monkeypatch.setattr(cli, "build_deps", build)
    with caplog.at_level(logging.INFO):
        assert cli.main(["--dry-run"]) == 0
    assert route.called
    assert any(r.name == "httpx" for r in caplog.records), "expected httpx request logs"
    captured = capsys.readouterr()
    assert SECRET not in caplog.text
    assert SECRET not in captured.out + captured.err


def test_build_deps_wires_real_adapters_on_one_client() -> None:
    settings = make_settings(mlh_season="2027", llm_call_budget=7)
    with httpx.Client() as client:
        deps = deps_module.build_deps(settings, client)
    assert deps.settings is settings
    assert isinstance(deps.llm, GroqLLM) and deps.llm.budget.limit == 7
    assert isinstance(deps.search, BraveSearch) and deps.search.client is client
    assert set(deps.sources) == {"devpost", "devfolio", "mlh"}
    devpost, devfolio, mlh = (deps.sources[k] for k in ("devpost", "devfolio", "mlh"))
    assert isinstance(devpost, DevpostSource) and devpost.client is client
    assert isinstance(devfolio, DevfolioSource) and devfolio.client is client
    assert isinstance(mlh, MLHSource) and mlh.client is client and mlh.season == "2027"
    assert isinstance(deps.notion, NotionGateway) and deps.notion.client is client
    assert isinstance(deps.fetcher, PageFetcher) and deps.fetcher.client is client
    assert deps.summary_writer is print


def test_build_deps_mlh_season_auto_by_default() -> None:
    with httpx.Client() as client:
        deps = deps_module.build_deps(make_settings(), client)
    mlh = deps.sources["mlh"]
    assert isinstance(mlh, MLHSource) and mlh.season is None


def test_build_deps_today_is_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    class FrozenDatetime:
        @staticmethod
        def now(tz: tzinfo | None = None) -> datetime:
            assert tz is UTC
            return datetime(2026, 12, 31, 23, 30, tzinfo=UTC)

    monkeypatch.setattr(deps_module, "datetime", FrozenDatetime)
    with httpx.Client() as client:
        deps = deps_module.build_deps(make_settings(), client)
    assert deps.today == date(2026, 12, 31)


def test_summary_writer_appends_to_github_step_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("before\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    write = deps_module.summary_writer_from_env()
    write("# Report")
    assert summary.read_text() == "before\n# Report\n"


def test_abort_log_and_summary_note_scrub_configured_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    key = "gsk_abort_secret_key"
    _set_env(monkeypatch, GROQ_API_KEY=key)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(cli, "build_deps", lambda settings, client: make_deps())

    def boom(deps: Deps) -> Any:
        raise RuntimeError(f"Error code: 401 - Authorization: Bearer {key}")

    monkeypatch.setattr(cli, "run", boom)
    with caplog.at_level(logging.INFO):
        assert cli.main([]) == 1
    assert "run aborted: RuntimeError: Error code: 401 - Authorization: Bearer REDACTED" in (
        caplog.text
    )
    assert key not in caplog.text
    assert key not in summary.read_text()


def test_debug_log_level_caps_sdk_loggers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setattr(cli, "build_deps", lambda *a: pytest.fail("must not build deps"))
    assert cli.main([]) == 2  # logging is configured before settings load
    for name in cli.NOISY_LOGGERS:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.INFO, name
    assert logging.getLogger("groq").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.INFO
    assert logging.getLogger("hackathon_sniper").getEffectiveLevel() == logging.DEBUG


def test_default_log_level_keeps_sdk_loggers_at_info() -> None:
    assert cli.main([]) == 2
    for name in (*cli.NOISY_LOGGERS, "httpx", "hackathon_sniper"):
        assert logging.getLogger(name).getEffectiveLevel() == logging.INFO, name


@pytest.mark.parametrize("kind", ["unreadable", "non-utf8"])
def test_unreadable_dotenv_exits_2_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    # (A directory named .env is silently skipped by pydantic-settings, so it can't fail.)
    dotenv = tmp_path / ".env"
    dotenv.write_bytes(b"GROQ_API_KEY=gsk_\xff\xfe_secret\n")
    if kind == "unreadable":
        if os.geteuid() == 0:
            pytest.skip("root can read a mode-000 file")
        dotenv.chmod(0)
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    text = captured.err + captured.out
    assert "Configuration error: could not load settings" in text
    assert "Traceback" not in text and "secret" not in text
