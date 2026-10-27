import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

USER_AGENT = "HackathonSniper/3.0 (+https://github.com/klee1611/HackathonSniper)"
_RELATED = re.compile(r"(rules|faq|prizes?|details|terms|eligibility)", re.I)


def make_client() -> httpx.Client:
    return httpx.Client(timeout=15.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


@dataclass
class Page:
    url: str
    text: str
    links: list[str] = field(default_factory=list)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def related_links(html: str, base_url: str) -> list[str]:
    """Same-site links that likely hold rules, prizes, or deadlines."""
    soup = BeautifulSoup(html, "html.parser")
    host = urlsplit(base_url).netloc
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base_url, str(anchor["href"])).split("#")[0]
        label = f"{anchor.get_text(' ', strip=True)} {href}"
        if (
            urlsplit(href).netloc == host
            and href != base_url
            and _RELATED.search(label)
            and href not in links
        ):
            links.append(href)
    return links


class PageFetcher:
    def __init__(self, client: httpx.Client, max_chars: int = 8000) -> None:
        self.client = client
        self.max_chars = max_chars
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
            response = self.client.get(f"{root}/robots.txt")
        except httpx.HTTPError:
            return False
        if response.status_code in (401, 403) or response.status_code >= 500:
            return False
        if response.status_code >= 400:
            return True
        rules = RobotFileParser()
        rules.parse(response.text.splitlines())
        return rules

    def fetch(self, url: str) -> Page | None:
        if not self.allowed(url):
            logger.info("robots.txt disallows %s", url)
            return None
        try:
            response = self.client.get(url)
        except httpx.HTTPError as e:
            logger.info("fetch failed for %s: %s", url, e)
            return None
        if response.status_code >= 400 or "html" not in response.headers.get("content-type", ""):
            return None
        final_url = str(response.url)
        return Page(
            url=final_url,
            text=html_to_text(response.text)[: self.max_chars],
            links=related_links(response.text, final_url),
        )
