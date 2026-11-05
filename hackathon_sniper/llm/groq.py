from typing import Any, TypeVar

import groq
from langchain_core.exceptions import OutputParserException
from langchain_groq import ChatGroq
from pydantic import BaseModel, SecretStr, ValidationError

from hackathon_sniper.llm.base import LLMBudget, LLMError, LLMUnavailable

T = TypeVar("T", bound=BaseModel)

# Errors that will fail every call in the run: rejected key, no access, retired/unknown model.
_FATAL = (groq.AuthenticationError, groq.PermissionDeniedError, groq.NotFoundError)


def _is_tool_use_failed(e: groq.BadRequestError) -> bool:
    """Groq rejects a malformed function call with HTTP 400, error code `tool_use_failed`."""
    error = e.body.get("error") if isinstance(e.body, dict) else None
    return isinstance(error, dict) and error.get("code") == "tool_use_failed"


class GroqLLM:
    def __init__(
        self, api_key: str, model: str, budget: LLMBudget, chat: Any | None = None
    ) -> None:
        self.budget = budget
        self.unavailable: str | None = None
        # The Groq SDK retries 429/5xx itself, honoring retry-after only up to 60s and
        # otherwise backing off exponentially; max_retries caps the retries.
        # Field names (not the `model`/`api_key` aliases) so mypy's pydantic plugin accepts them.
        self._chat = (
            chat
            if chat is not None
            else ChatGroq(
                model_name=model,
                groq_api_key=SecretStr(api_key),
                temperature=0,
                # Free tier is 8k tokens/minute, shared by parallel agents: 429s are routine.
                max_retries=5,
                # gpt-oss spends hidden reasoning tokens against that same per-minute quota.
                reasoning_effort="low" if model.startswith("openai/gpt-oss") else None,
            )
        )

    def structured(self, schema: type[T], system: str, user: str) -> T:
        if self.unavailable:
            raise LLMUnavailable(self.unavailable)
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
            except _FATAL as e:
                self.unavailable = f"Groq unavailable ({type(e).__name__}): {e}"
                raise LLMUnavailable(self.unavailable) from e
            except Exception as e:
                raise LLMError(str(e)) from e
        raise LLMError(f"invalid structured output: {last}") from last
