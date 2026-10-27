import pytest
from pydantic import ValidationError

from hackathon_sniper.config import Settings
from tests.factories import make_settings

REQUIRED = {
    "GROQ_API_KEY": "g",
    "NOTION_TOKEN": "n",
    "NOTION_HACKATHONS_DB_ID": "h",
    "NOTION_PARTICIPATIONS_DB_ID": "p",
    "NOTION_PROFILE_PAGE_ID": "pr",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for key, value in REQUIRED.items():
        monkeypatch.setenv(key, value)
    for key in ("SEARCH_PROVIDER", "BRAVE_API_KEY", "SERPAPI_API_KEY", "MAX_VERIFY", "DRY_RUN"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_reads_env_with_defaults(env: pytest.MonkeyPatch) -> None:
    env.setenv("BRAVE_API_KEY", "b")
    s = Settings(_env_file=None)
    assert (s.search_provider, s.max_verify, s.dry_run) == ("brave", 25, False)
    assert s.notion_hackathons_db_id == "h"


def test_requires_key_for_selected_provider(env: pytest.MonkeyPatch) -> None:
    env.setenv("SEARCH_PROVIDER", "serpapi")
    env.setenv("BRAVE_API_KEY", "b")
    with pytest.raises(ValidationError, match="SERPAPI"):
        Settings(_env_file=None)


def test_empty_env_value_is_treated_as_unset(env: pytest.MonkeyPatch) -> None:
    env.setenv("BRAVE_API_KEY", "b")
    env.setenv("MAX_VERIFY", "")
    assert Settings(_env_file=None).max_verify == 25


def test_empty_required_value_is_rejected(env: pytest.MonkeyPatch) -> None:
    env.setenv("BRAVE_API_KEY", "b")
    env.setenv("GROQ_API_KEY", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_errors_do_not_echo_secret_values(env: pytest.MonkeyPatch) -> None:
    env.setenv("GROQ_API_KEY", "sk-PLANTED")
    env.setenv("SEARCH_PROVIDER", "serpapi")
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    message = str(exc.value)
    # pydantic truncates long reprs ("sk-PLAN..."), so check the prefix too.
    assert "sk-PLANTED" not in message
    assert "sk-PLAN" not in message


@pytest.mark.parametrize("override", [{"llm_concurrency": 0}, {"max_verify": -1}])
def test_numeric_bounds(override: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        make_settings(**override)


def test_shell_env_does_not_leak_into_factories() -> None:
    # conftest clears Settings env vars, so e.g. SEARCH_PROVIDER=serpapi in the shell is ignored.
    s = make_settings()
    assert (s.search_provider, s.max_verify) == ("brave", 25)
