import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_\w+|ref|ref_src|source|fbclid|gclid|mc_cid|mc_eid)$", re.I)


def canonicalize(url: str) -> str:
    """Normalize a URL so the same event from different sources compares equal."""
    url = url.strip()
    if "://" not in url:
        url = f"https://{url}"
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)))
    return urlunsplit(("https", host, path, query, ""))
