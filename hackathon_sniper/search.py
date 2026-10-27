import logging
import re
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from hackathon_sniper.config import Settings

_REDACTED = "REDACTED"
_KEY_PARAM = re.compile(r"(api_key=)[^&\s\"'>]+")


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class SearchProvider(Protocol):
    def search(self, query: str, count: int) -> list[SearchResult]: ...


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


class _RedactApiKey(logging.Filter):
    """Mask `api_key=...` in httpx's request log line (SerpApi only takes the key as a param)."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "api_key=" in message:
            record.msg = _KEY_PARAM.sub(rf"\g<1>{_REDACTED}", message)
            record.args = None
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(f, _RedactApiKey) for f in _httpx_logger.filters):
    _httpx_logger.addFilter(_RedactApiKey())


class BraveSearch:
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, client: httpx.Client) -> None:
        self.api_key = api_key
        self.client = client

    def search(self, query: str, count: int) -> list[SearchResult]:
        response = self.client.get(
            self.URL,
            params={"q": query, "count": count},
            headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            follow_redirects=False,  # never forward the key header to another origin
        )
        response.raise_for_status()
        web: dict[str, Any] = response.json().get("web") or {}
        items: list[dict[str, Any]] = web.get("results") or []
        return [
            SearchResult(
                title=_strip_tags(item.get("title") or ""),
                url=item["url"],
                snippet=_strip_tags(item.get("description") or ""),
            )
            for item in items
            if item.get("url")
        ]


class SerpApiSearch:
    URL = "https://serpapi.com/search.json"

    def __init__(self, api_key: str, client: httpx.Client) -> None:
        self.api_key = api_key
        self.client = client

    def search(self, query: str, count: int) -> list[SearchResult]:
        params: dict[str, str | int] = {
            "engine": "google",
            "q": query,
            "num": count,
            "api_key": self.api_key,
        }
        safe = httpx.Request("GET", httpx.URL(self.URL, params=params | {"api_key": _REDACTED}))
        # The key travels in the query string, so httpx errors would echo it: re-raise scrubbed.
        try:
            response = self.client.get(self.URL, params=params, follow_redirects=False)
        except httpx.RequestError as e:
            message = str(e).replace(self.api_key, _REDACTED)
            raise type(e)(message, request=safe) from None
        if not response.is_success:
            response.request = safe
            raise httpx.HTTPStatusError(
                f"SerpApi returned HTTP {response.status_code} for {safe.url}",
                request=safe,
                response=response,
            )
        items: list[dict[str, Any]] = response.json().get("organic_results") or []
        return [
            SearchResult(
                title=item.get("title") or "", url=item["link"], snippet=item.get("snippet") or ""
            )
            for item in items
            if item.get("link")
        ]


def make_search(settings: Settings, client: httpx.Client) -> SearchProvider:
    if settings.search_provider == "serpapi":
        if not settings.serpapi_api_key:  # Settings validation guarantees this
            raise ValueError("SERPAPI_API_KEY is required when SEARCH_PROVIDER=serpapi")
        return SerpApiSearch(settings.serpapi_api_key, client)
    if not settings.brave_api_key:
        raise ValueError("BRAVE_API_KEY is required when SEARCH_PROVIDER=brave")
    return BraveSearch(settings.brave_api_key, client)
