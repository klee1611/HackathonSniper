import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "HackathonSniper/3.0 (+https://github.com/klee1611/HackathonSniper)"
_RELATED = re.compile(r"(rules|faq|prizes?|details|terms|eligibility)", re.I)
_ROBOTS_MAX_BYTES = 512_000


class BlockedURLError(httpx.RequestError):
    """A request was refused because it targets a non-public address or scheme."""


def _resolve(host: str) -> list[str]:
    """All IP addresses `host` resolves to. Module-level so tests can stub DNS."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _is_unsafe_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _guard_request(request: httpx.Request) -> None:
    """httpx request hook (runs on every redirect hop): refuse non-public targets."""
    url = request.url
    if url.scheme not in ("http", "https"):
        raise BlockedURLError(f"blocked scheme: {url}", request=request)
    host = url.host.lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".localhost"):
        raise BlockedURLError(f"blocked host: {url}", request=request)
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            addresses = _resolve(host)
        except (socket.gaierror, UnicodeError) as e:
            raise BlockedURLError(f"cannot resolve {host}: {e}", request=request) from e
    if not addresses or any(_is_unsafe_ip(a) for a in addresses):
        raise BlockedURLError(f"blocked non-public address: {url}", request=request)


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=15.0,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        max_redirects=5,
        event_hooks={"request": [_guard_request]},
    )


@dataclass
class Page:
    url: str
    text: str
    links: list[str] = field(default_factory=list)


def _soup_text(soup: BeautifulSoup) -> str:
    """Visible text. Mutates `soup` (drops script/style), so derive links first."""
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _soup_related_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    host = urlsplit(base_url).netloc
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, str(anchor["href"])).split("#")[0]
        parts = urlsplit(href)
        # Match on text + path + query only, so a keyword in the hostname can't match every link.
        label = f"{anchor.get_text(' ', strip=True)} {parts.path} {parts.query}"
        if (
            parts.netloc == host
            and href != base_url
            and _RELATED.search(label)
            and href not in links
        ):
            links.append(href)
    return links


def html_to_text(html: str) -> str:
    return _soup_text(BeautifulSoup(html, "html.parser"))


def related_links(html: str, base_url: str) -> list[str]:
    """Same-site links that likely hold rules, prizes, or deadlines."""
    return _soup_related_links(BeautifulSoup(html, "html.parser"), base_url)


def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    body = bytearray()
    for chunk in response.iter_bytes():
        body += chunk
        if len(body) >= max_bytes:
            break
    return bytes(body[:max_bytes])


def _declared_length(response: httpx.Response) -> int | None:
    try:
        return int(response.headers["content-length"])
    except (KeyError, ValueError):
        return None


class PageFetcher:
    def __init__(
        self, client: httpx.Client, max_chars: int = 8000, max_bytes: int = 2_000_000
    ) -> None:
        self.client = client
        self.max_chars = max_chars
        self.max_bytes = max_bytes
        # Per-origin policy: a parsed robots.txt, or a blanket True (allow) / False (deny).
        self._robots: dict[str, RobotFileParser | bool] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            self._robots[root] = self._load_robots(root)
        policy = self._robots[root]
        if isinstance(policy, bool):
            return policy
        return policy.can_fetch(USER_AGENT, url)

    def _load_robots(self, root: str) -> RobotFileParser | bool:
        try:
            with self.client.stream("GET", f"{root}/robots.txt") as response:
                status = response.status_code
                body = _read_capped(response, _ROBOTS_MAX_BYTES) if status < 400 else b""
        except httpx.HTTPError:
            return False
        if status in (401, 403) or status >= 500:
            return False
        if status >= 400:
            return True
        rules = RobotFileParser()
        rules.parse(body.decode("utf-8", errors="replace").splitlines())
        return rules

    def fetch(self, url: str) -> Page | None:
        if not self.allowed(url):
            logger.info("robots.txt disallows %s", url)
            return None
        try:
            with self.client.stream("GET", url) as response:
                if response.status_code >= 400:
                    return None
                if "html" not in response.headers.get("content-type", ""):
                    return None
                length = _declared_length(response)
                if length is not None and length > self.max_bytes:
                    logger.info("skipping %s: %d bytes exceeds cap", url, length)
                    return None
                body = _read_capped(response, self.max_bytes)
                charset = response.charset_encoding
                final_url = str(response.url)
        except httpx.HTTPError as e:
            logger.info("fetch failed for %s: %s", url, e)
            return None
        # Bytes let BeautifulSoup honour <meta charset>; a header charset wins when present.
        markup: str | bytes = body
        if charset:
            try:
                markup = body.decode(charset, errors="replace")
            except LookupError:
                pass
        soup = BeautifulSoup(markup, "html.parser")
        links = _soup_related_links(soup, final_url)
        return Page(url=final_url, text=_soup_text(soup)[: self.max_chars], links=links)
