import httpx
import respx

from hackathon_sniper.http import PageFetcher, make_client

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
