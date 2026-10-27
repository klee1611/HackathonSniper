import gzip
import ipaddress
import socket
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

import hackathon_sniper.http as http_module
from hackathon_sniper.http import (
    BlockedURLError,
    PageFetcher,
    _guard_peer,
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


# --- malformed URLs never escape fetch() ---------------------------------------


@pytest.mark.parametrize(
    "url", ["https://xn--.com/", "http://0177.0.0.1/x", "http://ex.com/\x01x", "http://ex\x01.com/"]
)
@respx.mock(assert_all_called=False)
def test_malformed_urls_return_none(url: str, respx_mock: respx.MockRouter) -> None:
    # http://ex.com/robots.txt is itself valid, so it may be fetched; the page never is.
    respx_mock.get("http://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    other = respx_mock.route()
    assert PageFetcher(make_client()).fetch(url) is None
    assert not other.called


# --- global-IP allowlist -------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://168.63.129.16/",  # Azure WireServer
        "http://[2002:a9fe:a9fe::]/",  # 6to4 wrapping 169.254.169.254
        "http://[64:ff9b::a9fe:a9fe]/",  # NAT64 wrapping 169.254.169.254
        "http://[2001:0:4136:e378:8000:63bf:5601:5601]/",  # Teredo client 169.254.169.254
        "http://224.0.1.1/",  # multicast
    ],
)
def test_guard_blocks_non_global_and_embedded_addresses(url: str) -> None:
    with pytest.raises(BlockedURLError):
        _guard_request(httpx.Request("GET", url))


def test_guard_allows_global_ip() -> None:
    _guard_request(httpx.Request("GET", "http://93.184.216.34/"))


# --- post-connect peer check ------------------------------------------------------


class _FakeNetworkStream:
    def __init__(self, addr: Any) -> None:
        self.addr = addr

    def get_extra_info(self, name: str) -> Any:
        return self.addr if name == "server_addr" else None


def _response(extensions: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200, request=httpx.Request("GET", "https://ex.com/"), extensions=extensions
    )


def test_peer_check_blocks_loopback_peer() -> None:
    with pytest.raises(BlockedURLError):
        _guard_peer(_response({"network_stream": _FakeNetworkStream(("127.0.0.1", 80))}))


def test_peer_check_allows_public_peer() -> None:
    _guard_peer(_response({"network_stream": _FakeNetworkStream(("93.184.216.34", 443))}))


def test_peer_check_skips_when_unknown() -> None:
    _guard_peer(_response({}))
    _guard_peer(_response({"network_stream": _FakeNetworkStream(None)}))


def test_client_installs_peer_hook() -> None:
    assert _guard_peer in make_client().event_hooks["response"]


# --- bounded decompression ---------------------------------------------------------


@respx.mock
def test_gzip_bomb_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    bomb = gzip.compress(b"<html><body>" + b"a" * 50_000_000, compresslevel=9)
    parsed: list[int] = []
    real_soup = http_module.BeautifulSoup

    def spy(markup: Any, *args: Any, **kwargs: Any) -> Any:
        parsed.append(len(markup))
        return real_soup(markup, *args, **kwargs)

    monkeypatch.setattr(http_module, "BeautifulSoup", spy)
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    route = respx.get("https://ex.com/hack").mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": "text/html", "Content-Encoding": "gzip"}, content=bomb
        )
    )
    page = PageFetcher(make_client(), max_bytes=100_000).fetch("https://ex.com/hack")
    assert page is not None and page.text.startswith("aaaa")
    assert parsed and max(parsed) <= 100_000
    assert route.calls.last.request.headers["accept-encoding"] == "identity"


@respx.mock
def test_small_gzip_page_decodes() -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8", "Content-Encoding": "gzip"},
            content=gzip.compress(HTML.encode()),
        )
    )
    page = PageFetcher(make_client()).fetch("https://ex.com/hack")
    assert page is not None and "Big Hack Deadline: Nov 1" in page.text


@respx.mock
def test_unsupported_content_encoding_returns_none() -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": "text/html", "Content-Encoding": "br"}, content=b"xx"
        )
    )
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is None


@respx.mock(assert_all_called=False)
def test_robots_with_unsupported_encoding_means_disallow(respx_mock: respx.MockRouter) -> None:
    respx_mock.get("https://ex.com/robots.txt").mock(
        return_value=httpx.Response(200, headers={"Content-Encoding": "br"}, content=b"xx")
    )
    route = respx_mock.get("https://ex.com/hack")
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is None
    assert not route.called


@respx.mock
def test_gzipped_robots_is_parsed() -> None:
    robots = gzip.compress(b"User-agent: *\nDisallow: /private\n")
    respx.get("https://ex.com/robots.txt").mock(
        return_value=httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=robots)
    )
    fetcher = PageFetcher(make_client())
    assert fetcher.allowed("https://ex.com/hack")
    assert not fetcher.allowed("https://ex.com/private/x")


# --- multibyte cut at the cap ----------------------------------------------------------


@respx.mock
def test_utf8_char_cut_at_cap_is_not_mojibake() -> None:
    head = '<html><head><meta charset="utf-8"></head><body><p>Café '.encode()
    body = head + "é".encode() * 100
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": "text/html"}, content=iter([body])
        )
    )
    # Streamed (no Content-Length), so the body is read and cut by the cap,
    # which lands on the first byte of the 3rd "é".
    page = PageFetcher(make_client(), max_bytes=len(head) + 5).fetch("https://ex.com/hack")
    assert page is not None
    assert page.text == "Café éé"


# --- logging ----------------------------------------------------------------------------


def test_guard_block_logs_reason_not_robots(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="hackathon_sniper.http")
    with respx.mock(assert_all_called=False):
        assert PageFetcher(make_client()).fetch("http://127.0.0.1/x") is None
    assert "robots.txt disallows" not in caplog.text
    assert "blocked" in caplog.text


# --- non-text charsets ------------------------------------------------------------


@pytest.mark.parametrize("charset", ["base64", "rot13", "zip"])
@respx.mock
def test_non_text_header_charset_falls_back(charset: str) -> None:
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(
        return_value=httpx.Response(
            200, headers={"Content-Type": f"text/html; charset={charset}"}, content=HTML.encode()
        )
    )
    page = PageFetcher(make_client()).fetch("https://ex.com/hack")
    assert page is not None and "Big Hack Deadline: Nov 1" in page.text


@respx.mock
def test_non_text_meta_charset_falls_back() -> None:
    body = b'<html><head><meta charset="base64"></head><body><p>Big Hack</p></body></html>'
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(
        return_value=httpx.Response(200, headers={"Content-Type": "text/html"}, content=body)
    )
    page = PageFetcher(make_client()).fetch("https://ex.com/hack")
    assert page is not None and "Big Hack" in page.text


@respx.mock
def test_parse_errors_return_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(body: bytes, header_charset: str | None) -> Any:
        raise LookupError("unknown encoding")

    monkeypatch.setattr(http_module, "_parse", broken)
    respx.get("https://ex.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://ex.com/hack").mock(return_value=httpx.Response(200, html=HTML))
    assert PageFetcher(make_client()).fetch("https://ex.com/hack") is None


# --- IPv4-compatible IPv6 -----------------------------------------------------------


@pytest.mark.parametrize("url", ["http://[::7f00:1]/", "http://[::a9fe:a9fe]/"])
def test_guard_blocks_ipv4_compatible_addresses(url: str) -> None:
    with pytest.raises(BlockedURLError):
        _guard_request(httpx.Request("GET", url))


def test_embedded_ips_unwraps_ipv4_compatible() -> None:
    found = http_module._embedded_ips(ipaddress.ip_address("::a9fe:a9fe"))
    assert ipaddress.IPv4Address("169.254.169.254") in found
    assert len(http_module._embedded_ips(ipaddress.ip_address("::1"))) == 1


# --- environment proxies ------------------------------------------------------------


def test_client_ignores_environment_proxies() -> None:
    assert make_client().trust_env is False
