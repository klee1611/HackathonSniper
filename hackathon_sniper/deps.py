import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

import httpx

from hackathon_sniper.config import Settings
from hackathon_sniper.http import Page, PageFetcher
from hackathon_sniper.llm.base import LLM, LLMBudget
from hackathon_sniper.llm.groq import GroqLLM
from hackathon_sniper.models import HackathonEvent
from hackathon_sniper.notion.client import NotionGateway
from hackathon_sniper.search import SearchProvider, make_search
from hackathon_sniper.sources.devfolio import DevfolioSource
from hackathon_sniper.sources.devpost import DevpostSource
from hackathon_sniper.sources.mlh import MLHSource

Source = Callable[[], list[HackathonEvent]]


class NotionPort(Protocol):
    def query_all(self, database_id: str) -> list[dict[str, Any]]: ...
    def get_page(self, page_id: str) -> dict[str, Any]: ...
    def get_page_text(self, page_id: str) -> str: ...
    def create_page(self, database_id: str, properties: dict[str, Any]) -> str: ...
    def update_page(self, page_id: str, properties: dict[str, Any]) -> None: ...


class FetcherPort(Protocol):
    def fetch(self, url: str) -> Page | None: ...


@dataclass
class Deps:
    """Everything a node may touch. Real wiring in build_deps (Task 22); fakes in tests."""

    settings: Settings
    llm: LLM
    search: SearchProvider
    sources: dict[str, Source]
    notion: NotionPort
    fetcher: FetcherPort
    today: date
    summary_writer: Callable[[str], None] = print


def summary_writer_from_env() -> Callable[[str], None]:
    """Write to the GitHub Actions job summary when running in Actions, else stdout."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return print

    def write(markdown: str) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")

    return write


def build_deps(settings: Settings, client: httpx.Client) -> Deps:
    """Production wiring. Every HTTP adapter shares `client`; the caller owns (and closes) it."""
    return Deps(
        settings=settings,
        llm=GroqLLM(
            settings.groq_api_key, settings.groq_model, LLMBudget(settings.llm_call_budget)
        ),
        search=make_search(settings, client),
        sources={
            "devpost": DevpostSource(client),
            "devfolio": DevfolioSource(client),
            "mlh": MLHSource(client, settings.mlh_season),
        },
        notion=NotionGateway(settings.notion_token, client),
        fetcher=PageFetcher(client),
        today=datetime.now(UTC).date(),
        summary_writer=summary_writer_from_env(),
    )
