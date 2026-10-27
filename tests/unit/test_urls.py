from hackathon_sniper.urls import canonicalize


def test_strips_tracking_www_fragment_and_trailing_slash() -> None:
    url = "HTTP://www.Devpost.com/hack/?utm_source=x&b=2&a=1#top"
    assert canonicalize(url) == "https://devpost.com/hack?a=1&b=2"


def test_root_url_has_no_trailing_slash() -> None:
    assert (
        canonicalize("https://surat-hack-2026.devfolio.co/")
        == "https://surat-hack-2026.devfolio.co"
    )


def test_adds_scheme_when_missing() -> None:
    assert canonicalize("hackcbs.tech") == "https://hackcbs.tech"


def test_is_idempotent() -> None:
    once = canonicalize("https://x.devpost.com/?ref=home")
    assert canonicalize(once) == once
