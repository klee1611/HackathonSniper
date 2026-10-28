from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,  # a secret pasted with a trailing newline still works
    )

    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"

    notion_token: str
    notion_hackathons_db_id: str
    notion_participations_db_id: str
    notion_profile_page_id: str

    search_provider: Literal["brave", "serpapi"] = "brave"
    brave_api_key: str | None = None
    serpapi_api_key: str | None = None

    mlh_season: str | None = None
    max_queries: int = Field(default=10, ge=1)
    results_per_query: int = Field(default=10, ge=1)
    max_verify: int = Field(default=15, ge=0)
    llm_call_budget: int = Field(default=60, ge=1)
    llm_concurrency: int = Field(default=2, ge=1)
    # LLM calls verify leaves unspent so the Ranker can still score this run's events.
    rank_budget_reserve: int = Field(default=8, ge=0)
    reverify_after_days: int = Field(default=7, ge=0)
    dry_run: bool = False

    @model_validator(mode="after")
    def _search_key_present(self) -> Self:
        key = self.brave_api_key if self.search_provider == "brave" else self.serpapi_api_key
        if not key:
            provider = self.search_provider
            raise ValueError(
                f"{provider.upper()}_API_KEY is required when SEARCH_PROVIDER={provider}"
            )
        return self
