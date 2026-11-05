from typing import Any

import groq
import httpx
import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel

from hackathon_sniper.llm.base import BudgetExceeded, LLMBudget, LLMError, LLMUnavailable
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


def bad_request(code: str) -> groq.BadRequestError:
    body = {"error": {"message": "failed", "type": "invalid_request_error", "code": code}}
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(400, request=request, json=body)
    return groq.BadRequestError(f"Error code: 400 - {body}", response=response, body=body)


def test_repairs_missing_tool_call() -> None:
    llm, _ = make([None, Answer(value=3)])
    assert llm.structured(Answer, "sys", "user").value == 3
    assert llm.budget.used == 2


def test_gives_up_when_tool_call_stays_missing() -> None:
    llm, _ = make([None, None])
    with pytest.raises(LLMError, match="invalid structured output"):
        llm.structured(Answer, "sys", "user")


def test_rejects_non_schema_output() -> None:
    llm, _ = make([{"value": 1}, Answer(value=4)])
    assert llm.structured(Answer, "sys", "user").value == 4
    assert llm.budget.used == 2


def test_repairs_groq_tool_use_failed() -> None:
    llm, chat = make([bad_request("tool_use_failed"), Answer(value=5)])
    assert llm.structured(Answer, "sys", "user").value == 5
    assert llm.budget.used == 2
    assert "previous answer was invalid" in chat.prompts[1][1][1]


def test_other_bad_request_is_not_repaired() -> None:
    llm, _ = make([bad_request("model_not_found"), Answer(value=6)])
    with pytest.raises(LLMError, match="model_not_found"):
        llm.structured(Answer, "sys", "user")
    assert llm.budget.used == 1


def test_budget_remaining_is_floored_at_zero() -> None:
    budget = LLMBudget(2)
    assert budget.remaining == 2
    budget.take()
    assert budget.remaining == 1
    assert LLMBudget(0).remaining == 0
    budget.limit = 0
    assert budget.remaining == 0


def api_error(cls: type[groq.APIStatusError], status: int) -> groq.APIStatusError:
    body = {"error": {"message": "nope", "type": "invalid_request_error", "code": "x"}}
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status, request=request, json=body)
    return cls(f"Error code: {status}", response=response, body=body)


@pytest.mark.parametrize(
    ("cls", "status"),
    [(groq.NotFoundError, 404), (groq.AuthenticationError, 401), (groq.PermissionDeniedError, 403)],
)
def test_fatal_provider_error_marks_llm_unavailable_and_fails_fast(
    cls: type[groq.APIStatusError], status: int
) -> None:
    llm, chat = make([api_error(cls, status), Answer(value=1)])
    assert llm.unavailable is None
    with pytest.raises(LLMUnavailable):
        llm.structured(Answer, "sys", "user")
    assert llm.unavailable and cls.__name__ in llm.unavailable
    # Later calls fail without spending budget or contacting the provider.
    with pytest.raises(LLMUnavailable):
        llm.structured(Answer, "sys", "user")
    assert llm.budget.used == 1
    assert len(chat.prompts) == 1


def test_transient_provider_error_does_not_mark_unavailable() -> None:
    llm, _ = make([api_error(groq.InternalServerError, 500), Answer(value=2)])
    with pytest.raises(LLMError) as info:
        llm.structured(Answer, "sys", "user")
    assert not isinstance(info.value, LLMUnavailable)
    assert llm.unavailable is None
    assert llm.structured(Answer, "sys", "user").value == 2


@pytest.mark.parametrize(
    ("model", "effort"),
    [("openai/gpt-oss-120b", "low"), ("openai/gpt-oss-20b", "low"), ("qwen/qwen3.8-27b", None)],
)
def test_real_client_config(model: str, effort: str | None) -> None:
    chat = GroqLLM("gsk_test", model, LLMBudget(1))._chat
    assert chat.max_retries == 5
    assert chat.reasoning_effort == effort
