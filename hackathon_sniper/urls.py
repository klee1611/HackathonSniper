import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_\w+|ref|ref_src|source|fbclid|gclid|mc_cid|mc_eid)$", re.I)


def canonicalize(url: str) -> str:
    """Normalize a URL so the same event from different sources compares equal."""
    url = url.strip()
    if url.startswith("//"):
        url = f"https:{url}"
    elif "://" not in url:
        url = f"https://{url}"
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    if not host:
        raise ValueError(f"URL has no host: {url!r}")
    path = parts.path.rstrip("/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)))
    return urlunsplit(("https", host, path, query, ""))


def try_canonicalize(url: str | None) -> str | None:
    """Like canonicalize, but returns None instead of raising (for untrusted LLM URLs)."""
    if not url or not isinstance(url, str):
        return None
    try:
        return canonicalize(url)
    except ValueError:
        return None
