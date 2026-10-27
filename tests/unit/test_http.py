import socket
from collections.abc import Iterator

import httpx
import pytest
import respx

import hackathon_sniper.http as http_module
from hackathon_sniper.http import (
    BlockedURLError,
    PageFetcher,
    _guard_request,
    make_client,
    related_links,
)

HTML = """<html><head><script>var x = 1</script></head><body>
<h1>Big Hack</h1><p>Deadline:  Nov 1</p>
<a href="/hack/rules">Official Rules</a>
<a href="https://other.com/faq">FAQ elsewhere</a>
</body></html>"""


@respx.mock
def test_fetch_returns_clean_text_and_same_site_related_links() -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(return_value=httpx.Response(200, html=HTML))
    page = PageFetcher(make_client()).fetch("https://ex.com/hack")
    assert page is not None
    assert "var x" not in page.text
    assert "Big Hack Deadline: Nov 1" in page.text
    assert page.links == ["https://ex.com/hack/rules"]


@respx.mock(assert_all_called=False)
def test_respects_robots_disallow(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://ex.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    )
    route = respx_mock.get("https://ex.com/private/x")
    assert PageFetcher(make_client()).fetch("https://ex.com/private/x") is None
    assert not route.called


@respx.mock(assert_all_called=False)
def test_robots_server_error_means_disallow(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(503))
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is None


@respx.mock
def test_truncates_text() -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(return_value=httpx.Response(200, html=HTML))
    page = PageFetcher(make_client(), max_chars=5).fetch("https://ex.com/hack")
    assert page is not None and len(page.text) == 5


@respx.mock
def test_non_html_and_errors_return_none() -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/data").mock(return_value=httpx.Response(200, json={"a": 1}))
    respx.get("https://ex.com/gone").mock(return_value=httpx.Response(404))
    fetcher = PageFetcher(make_client())
    assert fetcher.fetch("https://ex.com/data") is None
    assert fetcher.fetch("https://ex.com/gone") is None


@respx.mock
def test_robots_rules_allow_other_paths() -> None:
    respx.get("https://ex.com/robots.txt").mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
    )
    respx.get("https://ex.com/hack").mock(return_value=httpx.Response(200, html=HTML))
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is not None


# --- robots.txt edge cases ---------------------------------------------------


@respx.mock(assert_all_called=False)
def test_robots_transport_error_means_disallow(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://ex.com/robots.txt").mock(side_effect=httpx.ConnectError("boom"))
    route = respx_mock.get("https://ex.com/hack")
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is None
    assert not route.called


@respx.mock(assert_all_called=False)
def test_robots_unauthorized_means_disallow(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(401))
    route = respx_mock.get("https://ex.com/hack")
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is None
    assert not route.called


@respx.mock
def test_robots_not_found_means_allow() -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(return_value=httpx.Response(200, html=HTML))
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is not None


# --- SSRF guard ----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest",
        "http://localhost/x",
        "ftp://x.com/a",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
        "http://0.0.0.0/x",
    ],
)
def test_guard_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(BlockedURLError):
        _guard_request(httpx.Request("GET", url))


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/x", "http://169.254.169.254/latest", "http://localhost/x"]
)
@respx.mock(assert_all_called=False)
def test_fetch_blocks_internal_hosts(url: str, respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(url__regex=r".*")
    assert PageFetcher(make_client()).fetch(url) is None
    assert not route.called


@respx.mock(assert_all_called=False)
def test_fetch_blocks_unsupported_scheme(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.route()
    assert PageFetcher(make_client()).fetch("ftp://x.com/a") is None
    assert not route.called


@respx.mock(assert_all_called=False)
def test_fetch_blocks_host_resolving_to_private_ip(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(http_module, "_resolve", lambda host: ["10.0.0.5"])
    route = respx_mock.route()
    assert PageFetcher(make_client()).fetch("https://intranet.ex.com/hack") is None
    assert not route.called


def test_guard_blocks_if_any_resolved_address_is_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_module, "_resolve", lambda host: ["93.184.216.34", "127.0.0.1"])
    with pytest.raises(BlockedURLError):
        _guard_request(httpx.Request("GET", "https://ex.com/"))


def test_guard_treats_resolution_failure_as_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(host: str) -> list[str]:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(http_module, "_resolve", fail)
    with pytest.raises(BlockedURLError):
        _guard_request(httpx.Request("GET", "https://nope.invalid/"))


@respx.mock(assert_all_called=False)
def test_fetch_blocks_redirect_to_metadata(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx_mock.get("https://ex.com/hack").mock(
        return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    metadata = respx_mock.get("http://169.254.169.254/")
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is None
    assert not metadata.called


@respx.mock
def test_public_fetch_passes_guard() -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(return_value=httpx.Response(200, html=HTML))
    page = PageFetcher(make_client()).fetch("https://ex.com/hack")
    assert page is not None and "Big Hack" in page.text


# --- body size cap and decoding -----------------------------------------------


class _ExplodingStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        raise AssertionError("body must not be read")


@respx.mock
def test_huge_content_length_returns_none_without_reading_body() -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Type": "text/html", "Content-Length": "999999999"},
            stream=_ExplodingStream(),
        )
    )
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is None


@respx.mock
def test_body_without_content_length_is_capped() -> None:
    head = b"<html><body><p>Head part</p>"
    consumed: list[int] = []

    def chunks() -> Iterator[bytes]:
        yield head
        for i in range(1000):
            consumed.append(i)
            yield b"<p>TAIL</p>"

    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(
        return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, content=chunks())
    )
    page = PageFetcher(make_client(), max_bytes=len(head) + 5).fetch("https://ex.com/hack")
    assert page is not None
    assert "Head part" in page.text
    assert "TAIL" not in page.text
    assert len(consumed) < 1000


@respx.mock
def test_meta_charset_is_honoured() -> None:
    body = (
        b'<html><head><meta charset="windows-1252"></head><body><p>Caf\xe9 hack</p></body></html>'
    )
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(
        return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, content=body)
    )
    page = PageFetcher(make_client()).fetch("https://ex.com/hack")
    assert page is not None and "Café hack" in page.text


@respx.mock
def test_redirect_sets_final_url() -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/old").mock(
        return_value=httpx.Response(302, headers={"Location": "https://ex.com/new"})
    )
    respx.get("https://ex.com/new").mock(return_value=httpx.Response(200, html=HTML))
    page = PageFetcher(make_client()).fetch("https://ex.com/old")
    assert page is not None and page.url == "https://ex.com/new"


# --- related-link matching ------------------------------------------------------


def test_related_links_ignore_keywords_in_host() -> None:
    html = '<a href="/login">Sign in</a><a href="/rules">Read</a>'
    assert related_links(html, "https://rules-hack.ex.com/") == ["https://rules-hack.ex.com/rules"]


def test_related_links_match_query() -> None:
    html = '<a href="/event?tab=prizes">More</a>'
    assert related_links(html, "https://ex.com/event") == ["https://ex.com/event?tab=prizes"]
