from datetime import date
from typing import Any

from hackathon_sniper.config import Settings
from hackathon_sniper.deps import Deps, Source
from hackathon_sniper.models import HackathonEvent, Profile
from hackathon_sniper.notion.mapping import profile_to_properties
from tests.fakes import FakeFetcher, FakeLLM, FakeNotion, FakeSearch

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


def make_deps(
    *,
    llm: FakeLLM | None = None,
    search: FakeSearch | None = None,
    sources: dict[str, Source] | None = None,
    notion: FakeNotion | None = None,
    fetcher: FakeFetcher | None = None,
    **settings: Any,
) -> Deps:
    resolved = make_settings(**settings)
    notion = notion or FakeNotion()
    notion.add_database(resolved.notion_hackathons_db_id)
    notion.add_database(resolved.notion_participations_db_id)
    if resolved.notion_profile_page_id not in notion.pages:
        notion.add_page(
            None,
            profile_to_properties(Profile(interests=["AI"])),
            page_id=resolved.notion_profile_page_id,
        )
    return Deps(
        settings=resolved,
        llm=llm or FakeLLM(),
        search=search or FakeSearch({}),
        sources=sources if sources is not None else {},
        notion=notion,
        fetcher=fetcher or FakeFetcher({}),
        today=TODAY,
        summary_writer=lambda md: None,
    )
