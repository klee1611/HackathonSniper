# Verifier eval cases

Each case is `<name>.json` + `<name>.html` (HTML is git-ignored — third-party pages stay local).

1. Save a page: `curl -sL <event-url> > evals/cases/<name>.html`
2. Read it yourself and write `<name>.json`:
   {"url": "<event-url>", "expected": {"registration_deadline": "2026-11-01", "prize_usd": 10000, "format": "Online", "solo_allowed": "Yes"}}
   Use null when the page genuinely doesn't say. Omit a field to leave it unscored.
   `format` is one of Online / In-person / Hybrid / Unknown; `solo_allowed` one of Yes / No / Unknown.

The eval scores the main page only, not the follow-up rules/FAQ page the Verifier may also read.

Cases without an `.html` file are skipped (e.g. `example.json` on a fresh checkout).

Run: `uv run python evals/extraction_eval.py` (real Groq API; needs only `GROQ_API_KEY`,
optional `GROQ_MODEL`; at most 500 LLM calls).

Aim for ~20 varied pages (Devpost, Devfolio, MLH member sites, custom sites).
