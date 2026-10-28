"""Static security checks for the GitHub Actions workflows."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOW_FILES = sorted(WORKFLOWS_DIR.glob("*.yml"))
PINNED_USES = re.compile(r"^[\w.-]+/[\w./-]+@([0-9a-f]{40}|v\d+\.\d+\.\d+)$")


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in workflow["jobs"].values() for step in job.get("steps", [])]


def test_expected_workflows_exist() -> None:
    assert {p.name for p in WORKFLOW_FILES} >= {"ci.yml", "discover.yml", "smoke.yml"}


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_top_level_permissions_read_only(path: Path) -> None:
    assert _load(path)["permissions"] == {"contents": "read"}


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_run_scripts_do_not_interpolate_expressions(path: Path) -> None:
    for step in _steps(_load(path)):
        assert "${{" not in step.get("run", ""), step


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_actions_are_pinned(path: Path) -> None:
    for step in _steps(_load(path)):
        if "uses" in step:
            assert PINNED_USES.match(step["uses"]), step["uses"]


@pytest.mark.parametrize("path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_checkout_does_not_persist_credentials(path: Path) -> None:
    for step in _steps(_load(path)):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step.get("with", {}).get("persist-credentials") is False


def test_ci_has_no_secrets() -> None:
    assert "secrets." not in (WORKFLOWS_DIR / "ci.yml").read_text()
