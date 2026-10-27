from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from hackathon_sniper.llm.base import LLMBudget

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
