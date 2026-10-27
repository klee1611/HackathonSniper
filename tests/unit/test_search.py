import importlib
import logging
import traceback

import httpx
import pytest
import respx

from hackathon_sniper import search as search_module
from hackathon_sniper.http import make_client
from hackathon_sniper.search import BraveSearch, SearchResult, SerpApiSearch, make_search
from tests.factories import make_settings

SECRET = "sk-secret-123"


@respx.mock
def test_brave_maps_results_and_strips_html() -> None:
    route = respx.get(url__startswith=BraveSearch.URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Big Hack",
                            "url": "https://bighack.dev",
                            "description": "An <strong>AI</strong> hackathon",
                        }
                    ]
                }
            },
        )
    )
    results = BraveSearch("key", make_client()).search("ai hackathon", 5)
    assert results == [
        SearchResult(title="Big Hack", url="https://bighack.dev", snippet="An AI hackathon")
    ]
    request = route.calls.last.request
    assert request.headers["X-Subscription-Token"] == "key"
    assert request.url.params["q"] == "ai hackathon"
    assert request.url.params["count"] == "5"


@respx.mock
def test_brave_tolerates_missing_web_section_and_urls() -> None:
    respx.get(url__startswith=BraveSearch.URL).mock(
        return_value=httpx.Response(200, json={"web": None})
    )
    assert BraveSearch("key", make_client()).search("q", 5) == []
    respx.get(url__startswith=BraveSearch.URL).mock(
        return_value=httpx.Response(200, json={"web": {"results": [{"title": "no url"}]}})
    )
    assert BraveSearch("key", make_client()).search("q", 5) == []


@respx.mock
def test_serpapi_maps_organic_results() -> None:
    route = respx.get(url__startswith=SerpApiSearch.URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "organic_results": [
                    {"title": "Big Hack", "link": "https://bighack.dev", "snippet": "AI"}
                ]
            },
        )
    )
    results = SerpApiSearch("key", make_client()).search("ai hackathon", 5)
    assert results == [SearchResult(title="Big Hack", url="https://bighack.dev", snippet="AI")]
    params = route.calls.last.request.url.params
    assert params["engine"] == "google"
    assert params["api_key"] == "key"
    assert params["num"] == "5"


@respx.mock
def test_raises_on_http_error() -> None:
    respx.get(url__startswith=BraveSearch.URL).mock(return_value=httpx.Response(429))
    with pytest.raises(httpx.HTTPStatusError):
        BraveSearch("key", make_client()).search("q", 5)


@respx.mock
def test_serpapi_http_error_does_not_leak_key() -> None:
    respx.get(url__startswith=SerpApiSearch.URL).mock(return_value=httpx.Response(401))
    with pytest.raises(httpx.HTTPStatusError) as info:
        SerpApiSearch(SECRET, make_client()).search("q", 5)
    assert SECRET not in str(info.value)
    assert SECRET not in str(info.value.request.url)
    assert info.value.response.status_code == 401
    assert info.value.__cause__ is None and info.value.__context__ is None
    assert SECRET not in str(info.value.response.request.url)


@respx.mock
def test_serpapi_transport_error_does_not_leak_key() -> None:
    respx.get(url__startswith=SerpApiSearch.URL).mock(
        side_effect=httpx.ConnectError(f"boom {SerpApiSearch.URL}?api_key={SECRET}")
    )
    with pytest.raises(httpx.ConnectError) as info:
        SerpApiSearch(SECRET, make_client()).search("q", 5)
    assert SECRET not in str(info.value)
    assert SECRET not in str(info.value.request.url)
    assert info.value.__context__ is None
    assert info.value.__cause__ is None
    assert SECRET not in repr(info.value)
    assert SECRET not in "".join(traceback.format_exception(info.value))


@respx.mock
def test_serpapi_key_redacted_from_httpx_request_log(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(url__startswith=SerpApiSearch.URL).mock(
        return_value=httpx.Response(200, json={"organic_results": []})
    )
    with caplog.at_level(logging.DEBUG):
        SerpApiSearch(SECRET, make_client()).search("q", 5)
    assert caplog.records, "expected httpx to log the request"
    assert SECRET not in caplog.text


def test_make_search_picks_provider() -> None:
    client = make_client()
    assert isinstance(make_search(make_settings(), client), BraveSearch)
    serp = make_settings(search_provider="serpapi", serpapi_api_key="s")
    assert isinstance(make_search(serp, client), SerpApiSearch)


@respx.mock
def test_brave_does_not_follow_redirects_with_key() -> None:
    respx.get(url__startswith=BraveSearch.URL).mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/"})
    )
    evil = respx.get("https://evil.example/").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        BraveSearch("key", make_client()).search("q", 5)
    assert not evil.called


@respx.mock
def test_serpapi_does_not_follow_redirects() -> None:
    respx.get(url__startswith=SerpApiSearch.URL).mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/"})
    )
    evil = respx.get("https://evil.example/").mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(httpx.HTTPStatusError) as info:
        SerpApiSearch(SECRET, make_client()).search("q", 5)
    assert SECRET not in str(info.value)
    assert not evil.called


def _redact_filters() -> list[logging.Filter | object]:
    return [
        f
        for f in logging.getLogger("httpx").filters
        if getattr(f, "_hackathon_sniper_redact", False)
    ]


def test_redact_filter_survives_reload() -> None:
    httpx_logger = logging.getLogger("httpx")
    before = list(httpx_logger.filters)
    try:
        importlib.reload(search_module)
        assert len(_redact_filters()) == 1
    finally:
        for f in list(httpx_logger.filters):
            httpx_logger.removeFilter(f)
        for f in before:
            httpx_logger.addFilter(f)
