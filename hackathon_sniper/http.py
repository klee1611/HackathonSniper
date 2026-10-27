import codecs
import ipaddress
import logging
import re
import socket
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from bs4.dammit import EncodingDetector

logger = logging.getLogger(__name__)

USER_AGENT = "HackathonSniper/3.0 (+https://github.com/klee1611/HackathonSniper)"
_RELATED = re.compile(r"(rules|faq|prizes?|details|terms|eligibility)", re.I)
_ROBOTS_MAX_BYTES = 512_000
# Ask for an uncompressed body so the size cap bounds what we actually process.
_IDENTITY = {"Accept-Encoding": "identity"}
# Errors that mean "this URL can't be fetched": transport/guard failures and malformed URLs
# (httpx.InvalidURL is not an HTTPError; IDNA and urlsplit raise ValueError).
_FETCH_ERRORS = (httpx.HTTPError, httpx.InvalidURL, ValueError)

_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")
_EXTRA_BLOCKED = frozenset({ipaddress.IPv4Address("168.63.129.16")})  # Azure WireServer


class BlockedURLError(httpx.RequestError):
    """A request was refused because it targets a non-public address or scheme."""


class _UnsupportedEncodingError(Exception):
    """The server ignored Accept-Encoding: identity with an encoding we won't decode."""


def _resolve(host: str) -> list[str]:
    """All IP addresses `host` resolves to. Module-level so tests can stub DNS."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _embedded_ips(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """`ip` plus any IPv4 address tunnelled inside it (mapped, compat, 6to4, Teredo, NAT64)."""
    found: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            found.append(ip.ipv4_mapped)
        if ip.sixtofour is not None:
            found.append(ip.sixtofour)
        if ip.teredo is not None:
            found.extend(ip.teredo)
        if int(ip) >> 32 == 0 and int(ip) > 1:  # IPv4-compatible ::a.b.c.d (not :: or ::1)
            found.append(ipaddress.IPv4Address(int(ip)))
        if ip in _NAT64:
            found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return found


def _is_unsafe_ip(address: str) -> bool:
    """Allowlist: only globally routable unicast addresses are safe."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop IPv6 zone id
    except ValueError:
        return True
    for candidate in _embedded_ips(ip):
        if (
            not candidate.is_global
            or candidate.is_multicast
            or candidate.is_reserved
            or candidate.is_loopback
            or candidate.is_link_local
            or candidate.is_private
            or candidate in _EXTRA_BLOCKED
        ):
            return True
    return False


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
            raise BlockedURLError(f"blocked: cannot resolve {host}: {e}", request=request) from e
    if not addresses or any(_is_unsafe_ip(a) for a in addresses):
        raise BlockedURLError(f"blocked non-public address: {url}", request=request)


def _guard_peer(response: httpx.Response) -> None:
    """httpx response hook: re-check the address actually connected to (DNS rebinding).

    Skipped when the transport doesn't expose the socket (mock transports, some proxies).
    """
    stream = response.extensions.get("network_stream")
    if stream is None:
        return
    addr = stream.get_extra_info("server_addr")
    if not addr:
        return
    peer = str(addr[0])
    if _is_unsafe_ip(peer):
        raise BlockedURLError(
            f"blocked non-public peer {peer} for {response.request.url}", request=response.request
        )


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=15.0,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        max_redirects=5,
        trust_env=False,  # env proxies would bypass the guard and trip the peer check
        event_hooks={"request": [_guard_request], "response": [_guard_peer]},
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
        try:
            href = urljoin(base_url, str(anchor["href"])).split("#")[0]
            parts = urlsplit(href)
        except ValueError:  # e.g. a malformed IPv6 literal in the page's markup
            continue
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


def _take(chunks: Iterator[bytes], limit: int) -> Iterator[bytes]:
    """Yield from `chunks` until `limit` bytes in total, then stop reading."""
    remaining = limit
    for chunk in chunks:
        if remaining <= 0:
            return
        yield chunk[:remaining]
        remaining -= len(chunk)


def _read_body(response: httpx.Response, max_bytes: int) -> bytes:
    """At most `max_bytes` of decoded body; raw input is capped at the same limit.

    Raises _UnsupportedEncodingError for encodings other than identity/gzip/deflate,
    and zlib.error for corrupt compressed data.
    """
    encoding = response.headers.get("content-encoding", "").strip().lower()
    raw = _take(response.iter_raw(), max_bytes)
    if encoding in ("", "identity"):
        return b"".join(raw)
    if encoding not in ("gzip", "x-gzip", "deflate"):
        raise _UnsupportedEncodingError(encoding)
    inflater = zlib.decompressobj(zlib.MAX_WBITS | 32)  # auto-detect gzip / zlib headers
    out = bytearray()
    for chunk in raw:
        # max_length bounds the output; 0 would mean "unlimited", so stop before that.
        out += inflater.decompress(chunk, max_bytes - len(out))
        if len(out) >= max_bytes or inflater.eof:
            break
    return bytes(out)


def _declared_length(response: httpx.Response) -> int | None:
    try:
        return int(response.headers["content-length"])
    except (KeyError, ValueError):
        return None


def _known_encoding(name: str | None) -> str | None:
    """Normalised name of a *text* codec, or None (unknown, or bytes-to-bytes like base64)."""
    if not name:
        return None
    try:
        info = codecs.lookup(name)
        if not getattr(info, "_is_text_encoding", True):
            return None
        b"".decode(info.name)  # raises LookupError for non-text codecs
    except LookupError:
        return None
    return info.name


def _trim_partial_utf8(body: bytes) -> bytes:
    """Drop a trailing UTF-8 sequence cut off by the size cap (at most 3 bytes)."""
    for back in range(1, min(4, len(body)) + 1):
        byte = body[-back]
        if byte & 0xC0 == 0x80:  # continuation byte: keep looking for the lead byte
            continue
        if byte < 0x80:
            return body
        needed = 4 if byte >= 0xF0 else 3 if byte >= 0xE0 else 2
        return body[:-back] if needed > back else body
    return body


def _parse(body: bytes, header_charset: str | None) -> BeautifulSoup:
    """Parse capped bytes, honouring the header charset, else the document's declared one."""
    charset = _known_encoding(header_charset)
    declared = charset or _known_encoding(
        EncodingDetector.find_declared_encoding(body, is_html=True)
    )
    if declared == "utf-8":
        body = _trim_partial_utf8(body)
    if charset:
        return BeautifulSoup(body.decode(charset, errors="replace"), "html.parser")
    return BeautifulSoup(body, "html.parser", from_encoding=declared)


class PageFetcher:
    def __init__(
        self, client: httpx.Client, max_chars: int = 8000, max_bytes: int = 2_000_000
    ) -> None:
        self.client = client
        self.max_chars = max_chars
        self.max_bytes = max_bytes
        # Per-origin policy: a parsed robots.txt, or a blanket True (allow) / False (deny).
        self._robots: dict[str, RobotFileParser | bool] = {}

    def _policy(self, url: str) -> RobotFileParser | bool:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in self._robots:
            self._robots[root] = self._load_robots(root)
        return self._robots[root]

    def allowed(self, url: str) -> bool:
        try:
            policy = self._policy(url)
        except ValueError:
            return False
        if isinstance(policy, bool):
            return policy
        return policy.can_fetch(USER_AGENT, url)

    def _load_robots(self, root: str) -> RobotFileParser | bool:
        """Fetch robots.txt for `root`. Every reason for a blanket deny is logged here."""
        url = f"{root}/robots.txt"
        try:
            with self.client.stream("GET", url, headers=_IDENTITY) as response:
                status = response.status_code
                body = _read_body(response, _ROBOTS_MAX_BYTES) if status < 400 else b""
        except BlockedURLError as e:
            logger.info("%s", e)
            return False
        except (*_FETCH_ERRORS, _UnsupportedEncodingError, zlib.error) as e:
            logger.info("robots.txt unavailable for %s, treating as disallow: %r", root, e)
            return False
        if status in (401, 403) or status >= 500:
            logger.info("robots.txt for %s returned %d, treating as disallow", root, status)
            return False
        if status >= 400:
            return True
        rules = RobotFileParser()
        rules.parse(body.decode("utf-8", errors="replace").splitlines())
        return rules

    def fetch(self, url: str) -> Page | None:
        try:
            policy = self._policy(url)
        except ValueError as e:
            logger.info("invalid URL %r: %s", url, e)
            return None
        if policy is False:
            return None  # reason already logged by _load_robots
        if isinstance(policy, RobotFileParser) and not policy.can_fetch(USER_AGENT, url):
            logger.info("robots.txt disallows %s", url)
            return None
        try:
            with self.client.stream("GET", url, headers=_IDENTITY) as response:
                if response.status_code >= 400:
                    return None
                if "html" not in response.headers.get("content-type", ""):
                    return None
                length = _declared_length(response)
                if length is not None and length > self.max_bytes:
                    logger.info("skipping %s: %d bytes exceeds cap", url, length)
                    return None
                body = _read_body(response, self.max_bytes)
                header_charset = response.charset_encoding
                final_url = str(response.url)
        except BlockedURLError as e:
            logger.info("%s", e)
            return None
        except (*_FETCH_ERRORS, _UnsupportedEncodingError, zlib.error) as e:
            logger.info("fetch failed for %s: %r", url, e)
            return None
        try:
            soup = _parse(body, header_charset)
            links = _soup_related_links(soup, final_url)
            text = _soup_text(soup)[: self.max_chars]
        except (LookupError, ValueError) as e:
            logger.info("could not parse %s: %r", url, e)
            return None
        return Page(url=final_url, text=text, links=links)
