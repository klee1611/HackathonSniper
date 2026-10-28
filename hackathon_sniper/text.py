"""Sanitisers for untrusted text (web pages, search results, LLM output)."""

import unicodedata

# Bounds on what untrusted input can push into a prompt or into our data.
MAX_URL_CHARS = 500
MAX_NAME_CHARS = 200
MAX_THEMES = 10
MAX_THEME_CHARS = 50


def one_line(text: str, limit: int) -> str:
    # Drop control/format/bidi characters (category C*: NUL, RLO, zero-width, ...) first;
    # whitespace controls (\n, \t, \r) are kept so the split below turns them into spaces.
    text = "".join(c for c in text if c.isspace() or not unicodedata.category(c).startswith("C"))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip()


def prompt_text(text: str, limit: int) -> str:
    """Untrusted text for a prompt: one line, capped, unable to forge a <tag> delimiter."""
    return one_line(text.replace("<", " ").replace(">", " "), limit)
