import threading
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """The provider failed or returned unusable output."""


class BudgetExceeded(LLMError):
    """The per-run LLM call budget is spent."""


class LLMBudget:
    """Thread-safe call counter shared by every agent in a run (free-tier guard)."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def take(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise BudgetExceeded(f"LLM budget of {self.limit} calls exhausted")
            self.used += 1


class LLM(Protocol):
    budget: LLMBudget

    def structured(self, schema: type[T], system: str, user: str) -> T: ...
