import pytest

from hackathon_sniper.config import Settings


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real shell variables out of every test."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SSRF guard resolves hosts; pin every host to a public IP so no test hits DNS."""
    monkeypatch.setattr("hackathon_sniper.http._resolve", lambda host: ["93.184.216.34"])
