import argparse
import logging
import os
import sys
from typing import Any

from pydantic import ValidationError
from pydantic_settings import SettingsError

from hackathon_sniper.config import Settings
from hackathon_sniper.deps import build_deps, summary_writer_from_env
from hackathon_sniper.graph import run
from hackathon_sniper.http import make_client
from hackathon_sniper.nodes.report import exit_code

logger = logging.getLogger("hackathon_sniper")

EXIT_FAILED = 1
EXIT_CONFIG = 2
_MAX_ERROR_CHARS = 300
# Third-party loggers that log request/response bodies (full LLM prompts, which carry profile
# data and untrusted page text) at DEBUG. Never below INFO; at DEBUG they're held at WARNING.
NOISY_LOGGERS = (
    "groq",
    "openai",
    "httpcore",
    "langchain",
    "langchain_core",
    "langgraph",
    "urllib3",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hackathon_sniper",
        description="Discover, verify, rank, and sync hackathons to Notion.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run everything but skip Notion writes"
    )
    parser.add_argument("--max-verify", type=int, help="override MAX_VERIFY for this run")
    return parser


def _configure_logging() -> None:
    name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    level = logging.getLevelNamesMapping().get(name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("hackathon_sniper").setLevel(level)
    # httpx request lines (key params redacted by search.py) stay visible at DEBUG.
    logging.getLogger("httpx").setLevel(max(level, logging.INFO))
    noisy = logging.WARNING if level <= logging.DEBUG else max(level, logging.INFO)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(noisy)


def _config_error(e: ValidationError) -> str:
    """Field names and reasons only: Settings hides inputs, and we drop context too."""
    lines = ["Configuration error (check your environment variables or .env):"]
    for err in e.errors(include_url=False, include_input=False, include_context=False):
        field = ".".join(str(part) for part in err["loc"]).upper()
        lines.append(f"  {field}: {err['msg']}" if field else f"  {err['msg']}")
    return "\n".join(lines)


def _describe(e: BaseException, settings: Settings | None = None) -> str:
    message = str(e) if settings is None else settings.scrub(str(e))
    message = " ".join(message.split())[:_MAX_ERROR_CHARS]
    return f"{type(e).__name__}: {message}" if message else type(e).__name__


def _note_failure(markdown: str) -> None:
    """Best-effort note in the GitHub Actions job summary (no-op outside Actions)."""
    if not os.environ.get("GITHUB_STEP_SUMMARY"):
        return
    try:
        summary_writer_from_env()(markdown)
    except OSError as e:
        logger.warning("could not write job summary: %s", _describe(e))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)  # before Settings, so --help needs no env
    _configure_logging()

    overrides: dict[str, Any] = {}
    if args.dry_run:
        overrides["dry_run"] = True
    if args.max_verify is not None:
        overrides["max_verify"] = args.max_verify
    try:
        settings = Settings(**overrides)
    except ValidationError as e:
        print(_config_error(e), file=sys.stderr)
        _note_failure("## HackathonSniper: configuration error\n\nSee the job log for details.")
        return EXIT_CONFIG
    except (SettingsError, OSError, UnicodeDecodeError) as e:
        # e.g. an unreadable or non-UTF-8 .env; the message may quote file contents, so omit it.
        print(
            f"Configuration error: could not load settings ({type(e).__name__}); "
            "check that .env is a readable UTF-8 file.",
            file=sys.stderr,
        )
        _note_failure("## HackathonSniper: configuration error\n\nSee the job log for details.")
        return EXIT_CONFIG

    try:
        with make_client() as client:
            deps = build_deps(settings, client)
            return exit_code(run(deps), deps)
    except Exception as e:  # unattended run: a concise line, never a traceback with request data
        logger.error("run aborted: %s", _describe(e, settings))
        _note_failure(
            settings.scrub(
                f"## HackathonSniper: run aborted\n\n`{type(e).__name__}` before the report "
                "was written. See the job log for details."
            )
        )
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
