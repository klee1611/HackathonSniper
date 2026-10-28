"""Per-field accuracy of the Verifier on hand-labeled pages. Uses the real Groq API.

Measures production behaviour: the HTML goes through the fetcher's html_to_text and text cap,
then verify's own prompt and evidence checks (extract_page_facts). See evals/cases/README.md.
Needs only GROQ_API_KEY (and optionally GROQ_MODEL) from the environment or .env.

Run: uv run python evals/extraction_eval.py
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# Run as a file (`python evals/extraction_eval.py`), sys.path holds evals/, not the repo root
# where the (uninstalled) hackathon_sniper package lives.
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402

from hackathon_sniper.config import Settings  # noqa: E402
from hackathon_sniper.http import MAX_TEXT_CHARS, html_to_text  # noqa: E402
from hackathon_sniper.llm.base import LLM, BudgetExceeded, LLMBudget, LLMError  # noqa: E402
from hackathon_sniper.llm.groq import GroqLLM  # noqa: E402
from hackathon_sniper.models import HackathonEvent  # noqa: E402
from hackathon_sniper.nodes.verify import extract_page_facts  # noqa: E402

CASES = Path(__file__).parent / "cases"
EVAL_BUDGET = 500
FIELDS = ("registration_deadline", "prize_usd", "format", "solo_allowed")


class InvalidCase(ValueError):
    pass


def _load_case(case_file: Path) -> tuple[str, dict[str, Any]]:
    try:
        case = json.loads(case_file.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise InvalidCase(f"not valid JSON ({e})") from None
    if not isinstance(case, dict):
        raise InvalidCase("expected a JSON object")
    url, expected = case.get("url"), case.get("expected")
    if not isinstance(url, str) or not url:
        raise InvalidCase('"url" must be a non-empty string')
    if not isinstance(expected, dict) or not expected:
        raise InvalidCase('"expected" must be a non-empty object')
    unknown = sorted(set(expected) - set(FIELDS))
    if unknown:
        raise InvalidCase(f"unknown fields {unknown}; allowed: {list(FIELDS)}")
    return url, expected


def _observed(ev: HackathonEvent) -> dict[str, Any]:
    deadline = ev.registration_deadline
    return {
        "registration_deadline": deadline.isoformat() if deadline else None,
        "prize_usd": ev.prize_usd,
        "format": ev.format.value,
        "solo_allowed": ev.solo_allowed.value,
    }


def run_eval(cases_dir: Path, llm: LLM) -> str:
    """Evaluate every `<name>.json` + `<name>.html` case in `cases_dir`; return the report."""
    lines: list[str] = []
    totals: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    for case_file in sorted(cases_dir.glob("*.json")):
        name = case_file.stem
        html_file = case_file.with_suffix(".html")
        if not html_file.exists():
            lines.append(f"skip {name}: no {html_file.name}")
            continue
        try:
            url, expected = _load_case(case_file)
        except InvalidCase as e:
            lines.append(f"error {name}: invalid case: {e}")
            continue
        # Exactly what PageFetcher hands the Verifier: visible text, capped.
        text = html_to_text(html_file.read_text(encoding="utf-8", errors="replace"))
        stub = HackathonEvent(canonical_url=url, name=name)
        try:
            found = extract_page_facts(llm, stub, url, text[:MAX_TEXT_CHARS])
        except LLMError as e:
            lines.append(f"error {name}: {type(e).__name__}: {e}")
            if isinstance(e, BudgetExceeded):
                break
            continue
        got = _observed(found)
        for field in FIELDS:
            if field not in expected:
                continue
            want = expected[field]
            totals[field] += 1
            if got[field] == want:
                correct[field] += 1
            else:
                lines.append(f"mismatch {name}.{field}: expected {want!r}, got {got[field]!r}")
    lines.append("")
    if not totals:
        lines.append("no cases evaluated")
    for field in FIELDS:
        if totals[field]:
            ok, total = correct[field], totals[field]
            lines.append(f"{field:24} {ok}/{total} = {ok / total:.0%}")
    lines.append(f"LLM calls used: {llm.budget.used}/{llm.budget.limit}")
    return "\n".join(lines)


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GROQ_API_KEY is not set (environment or .env)")
    model = os.environ.get("GROQ_MODEL", "").strip() or Settings.model_fields["groq_model"].default
    llm = GroqLLM(api_key, model, LLMBudget(EVAL_BUDGET))
    print(run_eval(CASES, llm))


if __name__ == "__main__":
    main()
