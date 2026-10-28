"""Static security/hardening checks for the GitHub Actions workflows.

Each ``check_*`` helper takes the workflows directory so it can also be exercised against a
mutated copy (to confirm it really catches violations).
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
EXPECTED = ("ci.yml", "discover.yml", "smoke.yml")
SECRET_JOBS = ("discover.yml", "smoke.yml")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
PINNED_USES = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^\s+#\s*v\d+\.\d+\.\d+\s*$")


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def _jobs(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return list(workflow["jobs"].values())


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in _jobs(workflow) for step in job.get("steps", [])]


def _runs(workflow: dict[str, Any]) -> list[str]:
    return [step["run"] for step in _steps(workflow) if "run" in step]


def _files(wf_dir: Path) -> list[Path]:
    return sorted(wf_dir.glob("*.yml"))


def check_expected_workflows_exist(wf_dir: Path) -> None:
    assert {p.name for p in _files(wf_dir)} >= set(EXPECTED)


def check_permissions(wf_dir: Path) -> None:
    for path in _files(wf_dir):
        wf = _load(path)
        assert wf.get("permissions") == {"contents": "read"}, path.name
        for job in _jobs(wf):
            assert "permissions" not in job, f"{path.name}: job-level permissions"


def check_no_expression_in_run(wf_dir: Path) -> None:
    for path in _files(wf_dir):
        for run in _runs(_load(path)):
            assert "${{" not in run, f"{path.name}: {run!r}"


def check_actions_pinned(wf_dir: Path) -> None:
    for path in _files(wf_dir):
        for line in path.read_text().splitlines():
            m = USES_LINE.match(line)
            if m is None:
                continue
            ref, rest = m.groups()
            assert PINNED_USES.match(ref), f"{path.name}: {ref} not pinned to a full SHA"
            assert VERSION_COMMENT.match(rest), f"{path.name}: {ref} lacks '# vX.Y.Z' comment"


def check_checkout_no_persist_credentials(wf_dir: Path) -> None:
    for path in _files(wf_dir):
        for step in _steps(_load(path)):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step.get("with", {}).get("persist-credentials") is False, path.name


def check_ci_has_no_secrets(wf_dir: Path) -> None:
    assert "secrets." not in (wf_dir / "ci.yml").read_text()


def check_timeouts(wf_dir: Path) -> None:
    for path in _files(wf_dir):
        for job in _jobs(_load(path)):
            assert "timeout-minutes" in job, path.name


def check_concurrency(wf_dir: Path) -> None:
    discover = _load(wf_dir / "discover.yml")["concurrency"]
    smoke = _load(wf_dir / "smoke.yml")["concurrency"]
    assert discover["cancel-in-progress"] is False
    assert smoke["group"] != discover["group"]


def check_installs(wf_dir: Path) -> None:
    for path in _files(wf_dir):
        syncs = [r for r in _runs(_load(path)) if "uv sync" in r]
        assert syncs, f"{path.name}: no uv sync"
        for run in syncs:
            assert "--locked" in run, f"{path.name}: {run!r}"
            if path.name in SECRET_JOBS:
                assert "--no-dev" in run, f"{path.name}: {run!r}"


def check_no_cache_in_secret_jobs(wf_dir: Path) -> None:
    for name in SECRET_JOBS:
        setup_uv = [
            s
            for s in _steps(_load(wf_dir / name))
            if s.get("uses", "").startswith("astral-sh/setup-uv@")
        ]
        assert setup_uv, name
        for step in setup_uv:
            assert step.get("with", {}).get("enable-cache") is False, name


def check_schedules(wf_dir: Path) -> None:
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    discover_on = _load(wf_dir / "discover.yml")[True]
    assert discover_on["schedule"] == [{"cron": "17 0 * * *"}]
    assert any("--dry-run" in r for r in _runs(_load(wf_dir / "smoke.yml")))


CHECKS: list[Callable[[Path], None]] = [
    check_expected_workflows_exist,
    check_permissions,
    check_no_expression_in_run,
    check_actions_pinned,
    check_checkout_no_persist_credentials,
    check_ci_has_no_secrets,
    check_timeouts,
    check_concurrency,
    check_installs,
    check_no_cache_in_secret_jobs,
    check_schedules,
]


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_workflow_hardening(check: Callable[[Path], None]) -> None:
    check(WORKFLOWS_DIR)
