import pytest
from pydantic import ValidationError

from hackathon_sniper.config import Settings

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
