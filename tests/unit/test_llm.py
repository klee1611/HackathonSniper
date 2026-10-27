from typing import Any

import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel

from hackathon_sniper.llm.base import BudgetExceeded, LLMBudget, LLMError
from hackathon_sniper.llm.groq import GroqLLM


class Answer(BaseModel):
    value: int


class FakeChat:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.prompts: list[Any] = []

    def with_structured_output(self, schema: type[BaseModel]) -> "FakeChat":
        return self

    def invoke(self, messages: Any) -> Any:
        self.prompts.append(messages)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make(outcomes: list[Any], limit: int = 5) -> tuple[GroqLLM, FakeChat]:
    chat = FakeChat(outcomes)
    return GroqLLM("key", "model", LLMBudget(limit), chat=chat), chat


def test_budget_raises_when_spent() -> None:
    budget = LLMBudget(1)
    budget.take()
    with pytest.raises(BudgetExceeded):
        budget.take()
    assert budget.used == 1


def test_budget_exceeded_is_an_llm_error() -> None:
    assert issubclass(BudgetExceeded, LLMError)


def test_returns_structured_output() -> None:
    llm, _ = make([Answer(value=1)])
    assert llm.structured(Answer, "sys", "user") == Answer(value=1)
    assert llm.budget.used == 1


def test_repairs_invalid_output_once() -> None:
    llm, chat = make([OutputParserException("bad json"), Answer(value=2)])
    assert llm.structured(Answer, "sys", "user").value == 2
    assert llm.budget.used == 2
    assert "previous answer was invalid" in chat.prompts[1][1][1]


def test_gives_up_after_repair() -> None:
    llm, _ = make([OutputParserException("bad"), OutputParserException("bad")])
    with pytest.raises(LLMError, match="invalid structured output"):
        llm.structured(Answer, "sys", "user")


def test_wraps_provider_errors() -> None:
    llm, _ = make([RuntimeError("boom")])
    with pytest.raises(LLMError, match="boom"):
        llm.structured(Answer, "sys", "user")


def test_respects_budget() -> None:
    llm, _ = make([Answer(value=1)], limit=0)
    with pytest.raises(BudgetExceeded):
        llm.structured(Answer, "sys", "user")
