from typing import Any, TypeVar, cast

from langchain_core.exceptions import OutputParserException
from langchain_groq import ChatGroq
from pydantic import BaseModel, SecretStr, ValidationError

from hackathon_sniper.llm.base import LLMBudget, LLMError

T = TypeVar("T", bound=BaseModel)


class GroqLLM:
    def __init__(
        self, api_key: str, model: str, budget: LLMBudget, chat: Any | None = None
    ) -> None:
        self.budget = budget
        # The Groq SDK retries 429s itself and honors retry-after; max_retries caps that.
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
                return cast(T, runnable.invoke([("system", system), ("human", prompt)]))
            except (ValidationError, OutputParserException) as e:
                last = e
                prompt = (
                    f"{user}\n\nYour previous answer was invalid ({e}). Return valid JSON only."
                )
            except Exception as e:
                raise LLMError(str(e)) from e
        raise LLMError(f"invalid structured output: {last}") from last
