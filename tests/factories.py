from datetime import date
from typing import Any

from hackathon_sniper.config import Settings
from hackathon_sniper.models import HackathonEvent

TODAY = date(2026, 10, 26)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "groq_api_key": "test",
        "notion_token": "test",
        "notion_hackathons_db_id": "hackathons",
        "notion_participations_db_id": "participations",
        "notion_profile_page_id": "profile",
        "brave_api_key": "test",
    }
    return Settings(_env_file=None, **(base | overrides))


def event(url: str = "https://a.devpost.com", name: str = "A", **fields: Any) -> HackathonEvent:
    return HackathonEvent(canonical_url=url, name=name, **fields)
