import math
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

NOTION_VERSION = "2022-06-28"
_RETRYABLE = frozenset({429, 502, 503, 504})
# A 429 means Notion rejected the request unprocessed, so even a create is safe to resend.
# A 502/504 or a transport error may arrive after the write landed: never resend a create.
_RETRYABLE_UNSAFE = frozenset({429})
_MAX_DELAY = 60.0
# The connection was never established, so the request never left: safe to resend anything.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_REDACTED = "REDACTED"
_ID = re.compile(r"[A-Za-z0-9-]+")


class NotionError(Exception):
    pass


def _plain_text(rich: list[dict[str, Any]]) -> str:
    return "".join(
        t.get("plain_text") or (t.get("text") or {}).get("content") or ""
        for t in rich
        if isinstance(t, dict)
    )


def _created_id(data: dict[str, Any]) -> str:
    if not data.get("id"):
        raise NotionError("create response has no id")
    return str(data["id"])


def _id(value: str) -> str:
    """Refuse IDs that could steer a request to another endpoint (`/`, `?`, `..`)."""
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise NotionError(f"invalid Notion id: {repr(value)[:64]}")
    return value


def _retry_after(header: str | None) -> float | None:
    """Seconds from a numeric Retry-After, clamped; None if absent or unusable."""
    if header is None:
        return None
    try:
        seconds = float(header)
    except ValueError:  # HTTP-date or garbage
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return min(seconds, _MAX_DELAY)


class NotionGateway:
    BASE = "https://api.notion.com/v1"

    def __init__(
        self,
        token: str,
        client: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
        max_pages: int = 1000,
    ) -> None:
        self.client = client
        self.sleep = sleep
        self.max_retries = max_retries
        self.max_pages = max_pages
        self._token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base={self.BASE!r})"

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, _REDACTED) if self._token else text

    def _backoff(self, attempt: int, response: httpx.Response | None = None) -> float:
        header = response.headers.get("Retry-After") if response is not None else None
        delay = _retry_after(header)
        return delay if delay is not None else min(float(2**attempt), _MAX_DELAY)

    def _request(
        self,
        method: str,
        path: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
        idempotent: bool = True,
    ) -> dict[str, Any]:
        retryable = _RETRYABLE if idempotent else _RETRYABLE_UNSAFE
        for attempt in range(self.max_retries + 1):
            last = attempt >= self.max_retries
            failure: str | None = None
            try:
                response = self.client.request(
                    method,
                    f"{self.BASE}{path}",
                    headers=self._headers,
                    json=json,
                    params=params,
                    follow_redirects=False,  # never send the token to another origin
                )
            except httpx.TransportError as e:
                if (idempotent or isinstance(e, _NOT_SENT)) and not last:
                    self.sleep(self._backoff(attempt))
                    continue
                failure = f"{type(e).__name__}: {e}"
            except httpx.HTTPError as e:  # e.g. the SSRF guard; not worth retrying
                failure = f"{type(e).__name__}: {e}"
            if failure is not None:
                # Raised outside the except block so the chain can't carry the request object.
                raise NotionError(self._scrub(f"{method} {path} failed: {failure}"))
            if response.status_code in retryable and not last:
                self.sleep(self._backoff(attempt, response))
                continue
            if not response.is_success:
                raise NotionError(
                    self._scrub(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
                )
            try:
                result = response.json()
            except ValueError:
                result = None
            if not isinstance(result, dict):
                raise NotionError(f"{method} {path}: response is not a JSON object")
            return result
        raise NotionError(f"{method} {path}: retries exhausted")  # pragma: no cover

    def _paginate(self, method: str, path: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        cursor: str | None = None
        for _ in range(self.max_pages):
            page: dict[str, Any] = {"page_size": 100} | ({"start_cursor": cursor} if cursor else {})
            if method == "POST":
                data = self._request("POST", path, json=page)
            else:
                data = self._request("GET", path, params=page)
            batch = data.get("results")
            if not isinstance(batch, list):
                raise NotionError(f"{method} {path}: missing results list")
            results += batch
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise NotionError(f"{method} {path}: has_more without a new next_cursor")
            seen.add(cursor)
        raise NotionError(f"{method} {path}: more than {self.max_pages} pages")

    def query_all(self, database_id: str) -> list[dict[str, Any]]:
        return self._paginate("POST", f"/databases/{_id(database_id)}/query")

    def get_page(self, page_id: str) -> dict[str, Any]:
        return self._request("GET", f"/pages/{_id(page_id)}")

    def get_page_text(self, page_id: str) -> str:
        lines = []
        for block in self._paginate("GET", f"/blocks/{_id(page_id)}/children"):
            body = block.get(block.get("type") or "") if isinstance(block, dict) else None
            rich = body.get("rich_text") if isinstance(body, dict) else None
            if isinstance(rich, list) and rich:
                lines.append(_plain_text(rich))
        return "\n".join(lines)

    def create_page(self, database_id: str, properties: dict[str, Any]) -> str:
        data = self._request(
            "POST",
            "/pages",
            json={"parent": {"database_id": _id(database_id)}, "properties": properties},
            idempotent=False,
        )
        return _created_id(data)

    def update_page(self, page_id: str, properties: dict[str, Any]) -> None:
        self._request("PATCH", f"/pages/{_id(page_id)}", json={"properties": properties})

    def get_database(self, database_id: str) -> dict[str, Any]:
        return self._request("GET", f"/databases/{_id(database_id)}")

    def update_database(self, database_id: str, properties: dict[str, Any]) -> None:
        self._request("PATCH", f"/databases/{_id(database_id)}", json={"properties": properties})

    def create_database(self, parent_page_id: str, title: str, properties: dict[str, Any]) -> str:
        data = self._request(
            "POST",
            "/databases",
            json={
                "parent": {"type": "page_id", "page_id": _id(parent_page_id)},
                "title": [{"type": "text", "text": {"content": title}}],
                "properties": properties,
            },
            idempotent=False,
        )
        return _created_id(data)
