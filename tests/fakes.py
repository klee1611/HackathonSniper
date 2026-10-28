import copy
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from hackathon_sniper.http import Page
from hackathon_sniper.llm.base import LLMBudget
from hackathon_sniper.search import SearchResult

T = TypeVar("T", bound=BaseModel)


class FakeLLM:
    """Deterministic LLM: one handler per output schema, called with the user prompt."""

    def __init__(
        self,
        handlers: dict[type[Any], Callable[[str], Any]] | None = None,
        budget: LLMBudget | None = None,
    ) -> None:
        self.handlers = handlers or {}
        self.budget = budget or LLMBudget(1000)
        self.calls: list[tuple[type[Any], str]] = []

    def structured(self, schema: type[T], system: str, user: str) -> T:
        self.budget.take()
        self.calls.append((schema, user))
        if schema not in self.handlers:
            raise AssertionError(f"FakeLLM has no handler for {schema.__name__}")
        result: T = self.handlers[schema](user)
        return result


class FakeSearch:
    def __init__(self, results: dict[str, list[SearchResult]], fail: bool = False) -> None:
        self.results = results
        self.fail = fail
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, count: int) -> list[SearchResult]:
        self.calls.append((query, count))
        if self.fail:
            raise RuntimeError("search down")
        return self.results.get(query, [])[:count]


class FakeFetcher:
    def __init__(self, pages: dict[str, Page]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def fetch(self, url: str) -> Page | None:
        self.calls.append(url)
        return self.pages.get(url)


class FakeNotion:
    """In-memory Notion. update_page merges properties exactly like PATCH /pages.

    Reads return deep copies so callers can't mutate stored state; writes are recorded.
    """

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.page_text: dict[str, str] = {}
        self.writes: list[tuple[str, str]] = []
        self._next = 0

    def add_page(
        self,
        database_id: str | None,
        properties: dict[str, Any],
        page_id: str | None = None,
        text: str = "",
    ) -> str:
        """Seed a page without recording a write."""
        self._next += 1
        pid = page_id or f"page-{self._next}"
        self.pages[pid] = {
            "id": pid,
            "database_id": database_id,
            "properties": copy.deepcopy(properties),
        }
        self.page_text[pid] = text
        return pid

    def query_all(self, database_id: str) -> list[dict[str, Any]]:
        return [copy.deepcopy(p) for p in self.pages.values() if p["database_id"] == database_id]

    def get_page(self, page_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.pages[page_id])

    def get_page_text(self, page_id: str) -> str:
        return self.page_text.get(page_id, "")

    def create_page(self, database_id: str, properties: dict[str, Any]) -> str:
        pid = self.add_page(database_id, properties)
        self.writes.append(("create", pid))
        return pid

    def update_page(self, page_id: str, properties: dict[str, Any]) -> None:
        self.pages[page_id]["properties"].update(copy.deepcopy(properties))
        self.writes.append(("update", page_id))
