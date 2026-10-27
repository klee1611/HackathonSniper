import pytest

from hackathon_sniper.config import Settings


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real shell variables out of every test."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
