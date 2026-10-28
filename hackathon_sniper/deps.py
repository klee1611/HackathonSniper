from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Protocol

from hackathon_sniper.config import Settings
from hackathon_sniper.http import Page
from hackathon_sniper.llm.base import LLM
from hackathon_sniper.models import HackathonEvent
from hackathon_sniper.search import SearchProvider

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


if TYPE_CHECKING:
    from hackathon_sniper.http import PageFetcher
    from hackathon_sniper.notion.client import NotionGateway

    def _real_adapters_satisfy_ports(
        notion: NotionGateway, fetcher: PageFetcher
    ) -> tuple[NotionPort, FetcherPort]:
        """mypy-only: fails type-checking if the real classes drift from the ports."""
        return notion, fetcher
