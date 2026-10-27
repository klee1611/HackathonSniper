from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"

    notion_token: str
    notion_hackathons_db_id: str
    notion_participations_db_id: str
    notion_profile_page_id: str

    search_provider: Literal["brave", "serpapi"] = "brave"
    brave_api_key: str | None = None
    serpapi_api_key: str | None = None

    mlh_season: str = "2027"
    max_queries: int = 10
    results_per_query: int = 10
    max_verify: int = 25
    llm_call_budget: int = 60
    llm_concurrency: int = 2
    reverify_after_days: int = 7
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
