import json
import logging
import traceback

import httpx
import pytest
import respx

from hackathon_sniper.http import make_client
from hackathon_sniper.notion.client import NotionError, NotionGateway, _plain_text

BASE = "https://api.notion.com/v1"
SECRET = "secret_tok-123"


def gateway(sleeps: list[float] | None = None, token: str = "tok") -> NotionGateway:
    return NotionGateway(token, make_client(), sleep=(sleeps if sleeps is not None else []).append)


@respx.mock
def test_query_all_paginates_with_headers() -> None:
    route = respx.post(f"{BASE}/databases/db1/query").mock(
        side_effect=[
            httpx.Response(
                200, json={"results": [{"id": "a"}], "has_more": True, "next_cursor": "c1"}
            ),
            httpx.Response(
                200, json={"results": [{"id": "b"}], "has_more": False, "next_cursor": None}
            ),
        ]
    )
    assert [r["id"] for r in gateway().query_all("db1")] == ["a", "b"]
    assert json.loads(route.calls[1].request.content)["start_cursor"] == "c1"
    headers = route.calls[0].request.headers
    assert headers["Notion-Version"] == "2022-06-28"
    assert headers["Authorization"] == "Bearer tok"


@respx.mock
def test_retries_rate_limit_honoring_retry_after() -> None:
    sleeps: list[float] = []
    respx.get(f"{BASE}/pages/p1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1.5"}),
            httpx.Response(200, json={"id": "p1"}),
        ]
    )
    assert gateway(sleeps).get_page("p1") == {"id": "p1"}
    assert sleeps == [1.5]


@respx.mock
def test_client_error_raises() -> None:
    respx.get(f"{BASE}/pages/p1").mock(return_value=httpx.Response(400, json={"message": "bad"}))
    with pytest.raises(NotionError, match="400"):
        gateway().get_page("p1")


@respx.mock
def test_get_page_text_joins_rich_text_blocks() -> None:
    respx.get(url__startswith=f"{BASE}/blocks/p1/children").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "Hello"}]}},
                    {"type": "image", "image": {}},
                    {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "World"}]}},
                ],
                "has_more": False,
            },
        )
    )
    assert gateway().get_page_text("p1") == "Hello\nWorld"


@respx.mock
def test_create_and_update_page() -> None:
    create = respx.post(f"{BASE}/pages").mock(return_value=httpx.Response(200, json={"id": "new"}))
    update = respx.patch(f"{BASE}/pages/new").mock(
        return_value=httpx.Response(200, json={"id": "new"})
    )
    g = gateway()
    assert g.create_page("db1", {"Name": {"title": []}}) == "new"
    g.update_page("new", {"Fit Score": {"number": 5}})
    assert json.loads(create.calls.last.request.content)["parent"] == {"database_id": "db1"}
    assert json.loads(update.calls.last.request.content) == {
        "properties": {"Fit Score": {"number": 5}}
    }


# --- additional coverage -------------------------------------------------------------------


def test_plain_text_prefers_plain_text_then_text_content() -> None:
    rich = [{"plain_text": "a"}, {"text": {"content": "b"}}, {"type": "mention"}]
    assert _plain_text(rich) == "ab"


@respx.mock
def test_get_page_text_paginates_with_get_params() -> None:
    route = respx.get(url__startswith=f"{BASE}/blocks/p1/children").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [
                        {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "1"}]}}
                    ],
                    "has_more": True,
                    "next_cursor": "c1",
                },
            ),
            httpx.Response(
                200,
                json={
                    "results": [{"type": "quote", "quote": {"rich_text": [{"plain_text": "2"}]}}],
                    "has_more": False,
                    "next_cursor": None,
                },
            ),
        ]
    )
    assert gateway().get_page_text("p1") == "1\n2"
    assert route.calls[0].request.url.params["page_size"] == "100"
    assert "start_cursor" not in route.calls[0].request.url.params
    assert route.calls[1].request.url.params["start_cursor"] == "c1"


@respx.mock
def test_database_endpoints() -> None:
    get = respx.get(f"{BASE}/databases/db1").mock(
        return_value=httpx.Response(200, json={"id": "db1", "properties": {}})
    )
    patch = respx.patch(f"{BASE}/databases/db1").mock(
        return_value=httpx.Response(200, json={"id": "db1"})
    )
    create = respx.post(f"{BASE}/databases").mock(
        return_value=httpx.Response(200, json={"id": "db2"})
    )
    g = gateway()
    assert g.get_database("db1") == {"id": "db1", "properties": {}}
    g.update_database("db1", {"Tags": {"multi_select": {}}})
    assert g.create_database("page1", "Hackathons", {"Name": {"title": {}}}) == "db2"
    assert get.called
    assert json.loads(patch.calls.last.request.content) == {
        "properties": {"Tags": {"multi_select": {}}}
    }
    assert json.loads(create.calls.last.request.content) == {
        "parent": {"type": "page_id", "page_id": "page1"},
        "title": [{"type": "text", "text": {"content": "Hackathons"}}],
        "properties": {"Name": {"title": {}}},
    }


@respx.mock
@pytest.mark.parametrize("status", [502, 503, 504])
def test_retries_server_errors_with_exponential_backoff(status: int) -> None:
    sleeps: list[float] = []
    respx.get(f"{BASE}/pages/p1").mock(
        side_effect=[
            httpx.Response(status),
            httpx.Response(status),
            httpx.Response(200, json={"id": "p1"}),
        ]
    )
    assert gateway(sleeps).get_page("p1") == {"id": "p1"}
    assert sleeps == [1.0, 2.0]


@respx.mock
def test_gives_up_after_max_retries() -> None:
    sleeps: list[float] = []
    route = respx.get(f"{BASE}/pages/p1").mock(return_value=httpx.Response(503, text="down"))
    with pytest.raises(NotionError, match="503"):
        gateway(sleeps).get_page("p1")
    assert route.call_count == 4  # 1 + max_retries
    assert len(sleeps) == 3


@respx.mock
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("100000", 60.0),  # clamped
        ("Wed, 21 Oct 2015 07:28:00 GMT", 1.0),  # HTTP-date: backoff
        ("soon", 1.0),
        ("-5", 1.0),
        ("nan", 1.0),
        ("inf", 1.0),
        ("", 1.0),
        ("0", 0.0),
    ],
)
def test_retry_after_is_bounded_and_tolerant(header: str, expected: float) -> None:
    sleeps: list[float] = []
    respx.get(f"{BASE}/pages/p1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": header}),
            httpx.Response(200, json={"id": "p1"}),
        ]
    )
    assert gateway(sleeps).get_page("p1") == {"id": "p1"}
    assert sleeps == [expected]


@respx.mock
def test_backoff_is_capped() -> None:
    sleeps: list[float] = []
    respx.get(f"{BASE}/pages/p1").mock(return_value=httpx.Response(503))
    g = NotionGateway("tok", make_client(), sleep=sleeps.append, max_retries=10)
    with pytest.raises(NotionError):
        g.get_page("p1")
    assert max(sleeps) == 60.0


@respx.mock
def test_transport_errors_are_retried_on_idempotent_calls() -> None:
    sleeps: list[float] = []
    respx.post(f"{BASE}/databases/db1/query").mock(
        side_effect=[
            httpx.ConnectTimeout("timed out"),
            httpx.Response(200, json={"results": [{"id": "a"}], "has_more": False}),
        ]
    )
    respx.patch(f"{BASE}/pages/p1").mock(
        side_effect=[httpx.ReadTimeout("timed out"), httpx.Response(200, json={"id": "p1"})]
    )
    g = gateway(sleeps)
    assert [r["id"] for r in g.query_all("db1")] == ["a"]
    g.update_page("p1", {})
    assert sleeps == [1.0, 1.0]


@respx.mock
def test_transport_errors_exhausted_raise_notion_error_without_token() -> None:
    respx.get(f"{BASE}/pages/p1").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(NotionError, match="ConnectError") as info:
        gateway(token=SECRET).get_page("p1")
    assert info.value.__cause__ is None
    assert info.value.__context__ is None
    assert SECRET not in "".join(traceback.format_exception(info.value))


@respx.mock
@pytest.mark.parametrize("call", ["create_page", "create_database"])
def test_creates_are_not_retried_on_transport_errors(call: str) -> None:
    sleeps: list[float] = []
    route = respx.post(url__regex=rf"{BASE}/(pages|databases)$").mock(
        side_effect=[httpx.ReadTimeout("timed out"), httpx.Response(200, json={"id": "x"})]
    )
    g = gateway(sleeps)
    with pytest.raises(NotionError, match="ReadTimeout"):
        if call == "create_page":
            g.create_page("db1", {})
        else:
            g.create_database("page1", "T", {})
    assert route.call_count == 1
    assert sleeps == []


@respx.mock
@pytest.mark.parametrize("status", [502, 504])
def test_creates_are_not_retried_on_ambiguous_gateway_errors(status: int) -> None:
    route = respx.post(f"{BASE}/pages").mock(
        side_effect=[httpx.Response(status), httpx.Response(200, json={"id": "x"})]
    )
    with pytest.raises(NotionError, match=str(status)):
        gateway().create_page("db1", {})
    assert route.call_count == 1


@respx.mock
def test_creates_are_retried_on_rate_limit() -> None:
    sleeps: list[float] = []
    route = respx.post(f"{BASE}/pages").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"id": "x"}),
        ]
    )
    assert gateway(sleeps).create_page("db1", {}) == "x"
    assert route.call_count == 2
    assert sleeps == [2.0]


@respx.mock
def test_error_messages_never_contain_token(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    respx.get(f"{BASE}/pages/p1").mock(
        return_value=httpx.Response(401, text=f"invalid token {SECRET}")
    )
    with pytest.raises(NotionError) as info:
        gateway(token=SECRET).get_page("p1")
    assert "401" in str(info.value)
    assert SECRET not in "".join(traceback.format_exception(info.value))
    assert SECRET not in caplog.text


def test_token_not_in_repr() -> None:
    g = gateway(token=SECRET)
    assert SECRET not in repr(g)


@respx.mock
def test_redirects_are_not_followed() -> None:
    respx.get(f"{BASE}/pages/p1").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"})
    )
    evil = respx.get("https://evil.example/x").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(NotionError, match="302"):
        gateway().get_page("p1")
    assert not evil.called


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        {"results": [{"id": "a"}], "has_more": True, "next_cursor": None},
        {"results": [{"id": "a"}], "has_more": True},
        {"results": [{"id": "a"}], "has_more": True, "next_cursor": ""},
    ],
)
def test_pagination_without_cursor_raises(body: dict[str, object]) -> None:
    route = respx.post(f"{BASE}/databases/db1/query").mock(
        return_value=httpx.Response(200, json=body)
    )
    with pytest.raises(NotionError, match="cursor"):
        gateway().query_all("db1")
    assert route.call_count == 1


@respx.mock
def test_pagination_with_repeated_cursor_raises() -> None:
    route = respx.post(f"{BASE}/databases/db1/query").mock(
        return_value=httpx.Response(
            200, json={"results": [{"id": "a"}], "has_more": True, "next_cursor": "same"}
        )
    )
    with pytest.raises(NotionError, match="cursor"):
        gateway().query_all("db1")
    assert route.call_count == 2


@respx.mock
def test_pagination_page_cap() -> None:
    counter = iter(range(10_000))
    route = respx.post(f"{BASE}/databases/db1/query").mock(
        side_effect=lambda request: httpx.Response(
            200, json={"results": [], "has_more": True, "next_cursor": f"c{next(counter)}"}
        )
    )
    g = NotionGateway("tok", make_client(), sleep=lambda s: None, max_pages=5)
    with pytest.raises(NotionError, match="pages"):
        g.query_all("db1")
    assert route.call_count == 5


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=[1, 2]),
    ],
)
def test_malformed_response_raises_notion_error(response: httpx.Response) -> None:
    respx.get(f"{BASE}/pages/p1").mock(return_value=response)
    with pytest.raises(NotionError):
        gateway().get_page("p1")


@respx.mock
def test_malformed_results_raise_notion_error() -> None:
    respx.post(f"{BASE}/databases/db1/query").mock(
        return_value=httpx.Response(200, json={"results": None, "has_more": False})
    )
    with pytest.raises(NotionError, match="results"):
        gateway().query_all("db1")


@pytest.mark.parametrize("bad", ["", "../users", "p1?x=1", "p1/children", "a b", "p1#x"])
def test_rejects_ids_that_could_escape_the_path(bad: str) -> None:
    with pytest.raises(NotionError, match="invalid"):
        gateway().get_page(bad)
    with pytest.raises(NotionError, match="invalid"):
        gateway().update_page(bad, {})


@respx.mock
def test_get_page_text_tolerates_odd_blocks() -> None:
    respx.get(url__startswith=f"{BASE}/blocks/p1/children").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    "junk",
                    {"type": None},
                    {"type": "paragraph", "paragraph": None},
                    {"type": "paragraph", "paragraph": {"rich_text": "x"}},
                    {
                        "type": "to_do",
                        "to_do": {"rich_text": [{"text": None}, {"plain_text": "ok"}]},
                    },
                ],
                "has_more": False,
            },
        )
    )
    assert gateway().get_page_text("p1") == "ok"


@respx.mock
def test_create_without_id_raises() -> None:
    respx.post(f"{BASE}/pages").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(NotionError, match="id"):
        gateway().create_page("db1", {})


@respx.mock
@pytest.mark.parametrize(
    "error", [httpx.ConnectError("refused"), httpx.ConnectTimeout("t"), httpx.PoolTimeout("t")]
)
def test_creates_are_retried_when_request_was_never_sent(error: httpx.TransportError) -> None:
    sleeps: list[float] = []
    route = respx.post(f"{BASE}/pages").mock(
        side_effect=[error, httpx.Response(200, json={"id": "x"})]
    )
    assert gateway(sleeps).create_page("db1", {}) == "x"
    assert route.call_count == 2
    assert sleeps == [1.0]


@respx.mock
@pytest.mark.parametrize(
    "error",
    [httpx.ReadTimeout("t"), httpx.WriteTimeout("t"), httpx.RemoteProtocolError("t")],
)
def test_creates_are_not_retried_when_request_may_have_been_sent(
    error: httpx.TransportError,
) -> None:
    route = respx.post(f"{BASE}/pages").mock(
        side_effect=[error, httpx.Response(200, json={"id": "x"})]
    )
    with pytest.raises(NotionError, match=type(error).__name__):
        gateway().create_page("db1", {})
    assert route.call_count == 1


@pytest.mark.parametrize("bad", [None, 123, b"p1", ["p1"]])
def test_rejects_non_string_ids(bad: object) -> None:
    with pytest.raises(NotionError, match="invalid"):
        gateway().get_page(bad)  # type: ignore[arg-type]
