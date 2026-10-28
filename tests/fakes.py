import copy
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from hackathon_sniper.http import Page
from hackathon_sniper.llm.base import LLMBudget
from hackathon_sniper.notion.client import NotionError
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
    """In-memory Notion that fails the way NotionGateway does.

    - update_page merges properties exactly like PATCH /pages (other columns survive).
    - Unknown database or page ids raise NotionError, like the real 404s.
    - Reads return deep copies so callers can't mutate stored state; writes are recorded.
    - All access is serialised by a lock (graph branches may run in threads).
    """

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.page_text: dict[str, str] = {}
        self.databases: set[str] = set()
        self.writes: list[tuple[str, str]] = []
        self._next = 0
        self._lock = threading.Lock()

    def add_database(self, database_id: str) -> None:
        with self._lock:
            self.databases.add(database_id)

    def add_page(
        self,
        database_id: str | None,
        properties: dict[str, Any],
        page_id: str | None = None,
        text: str = "",
    ) -> str:
        """Seed a page without recording a write. database_id=None means a standalone page."""
        with self._lock:
            return self._insert(database_id, properties, page_id, text)

    def _insert(
        self, database_id: str | None, properties: dict[str, Any], page_id: str | None, text: str
    ) -> str:
        if page_id is None:
            while True:  # never let a generated id overwrite a seeded page
                self._next += 1
                page_id = f"fake-{self._next:08d}"
                if page_id not in self.pages:
                    break
        if database_id is not None:
            self.databases.add(database_id)
        self.pages[page_id] = {
            "id": page_id,
            "database_id": database_id,
            "properties": copy.deepcopy(properties),
        }
        self.page_text[page_id] = text
        return page_id

    def _page(self, page_id: str) -> dict[str, Any]:
        page = self.pages.get(page_id)
        if page is None:
            raise NotionError(f"GET /pages/{page_id} -> 404: object_not_found")
        return page

    def query_all(self, database_id: str) -> list[dict[str, Any]]:
        with self._lock:
            if database_id not in self.databases:
                raise NotionError(f"POST /databases/{database_id}/query -> 404: object_not_found")
            return [
                copy.deepcopy(p) for p in self.pages.values() if p["database_id"] == database_id
            ]

    def get_page(self, page_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._page(page_id))

    def get_page_text(self, page_id: str) -> str:
        with self._lock:
            self._page(page_id)
            return self.page_text[page_id]

    def create_page(self, database_id: str, properties: dict[str, Any]) -> str:
        with self._lock:
            pid = self._insert(database_id, properties, None, "")
            self.writes.append(("create", pid))
            return pid

    def update_page(self, page_id: str, properties: dict[str, Any]) -> None:
        with self._lock:
            self._page(page_id)["properties"].update(copy.deepcopy(properties))
            self.writes.append(("update", page_id))
