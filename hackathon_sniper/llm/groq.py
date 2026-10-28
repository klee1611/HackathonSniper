from typing import Any, TypeVar

import groq
from langchain_core.exceptions import OutputParserException
from langchain_groq import ChatGroq
from pydantic import BaseModel, SecretStr, ValidationError

from hackathon_sniper.llm.base import LLMBudget, LLMError

T = TypeVar("T", bound=BaseModel)


def _is_tool_use_failed(e: groq.BadRequestError) -> bool:
    """Groq rejects a malformed function call with HTTP 400, error code `tool_use_failed`."""
    error = e.body.get("error") if isinstance(e.body, dict) else None
    return isinstance(error, dict) and error.get("code") == "tool_use_failed"


class GroqLLM:
    def __init__(
        self, api_key: str, model: str, budget: LLMBudget, chat: Any | None = None
    ) -> None:
        self.budget = budget
        # The Groq SDK retries 429/5xx itself, honoring retry-after only up to 60s and
        # otherwise backing off exponentially; max_retries caps the attempts.
        # Field names (not the `model`/`api_key` aliases) so mypy's pydantic plugin accepts them.
        self._chat = (
            chat
            if chat is not None
            else ChatGroq(
                model_name=model, groq_api_key=SecretStr(api_key), temperature=0, max_retries=2
            )
        )

    def structured(self, schema: type[T], system: str, user: str) -> T:
        runnable = self._chat.with_structured_output(schema)
        prompt = user
        last: Exception | None = None
        for _ in range(2):  # first attempt + one repair attempt
            self.budget.take()
            try:
                try:
                    result = runnable.invoke([("system", system), ("human", prompt)])
                except groq.BadRequestError as e:
                    if not _is_tool_use_failed(e):
                        raise
                    raise OutputParserException(f"malformed tool call: {e}") from e
                if not isinstance(result, schema):
                    # The tools parser yields None when the model makes no (valid) tool call.
                    raise OutputParserException(f"model returned no tool call (got {result!r})")
                return result
            except (ValidationError, OutputParserException) as e:
                last = e
                prompt = (
                    f"{user}\n\nYour previous answer was invalid ({e}). Return valid JSON only."
                )
            except Exception as e:
                raise LLMError(str(e)) from e
        raise LLMError(f"invalid structured output: {last}") from last
