"""One-time, idempotent Notion setup for HackathonSniper v3.

1. Adds missing agent-owned columns to the Hackathons DB (NOTION_HACKATHONS_DB_ID). Existing
   columns are never deleted or retyped; only a title column not called "Name" is renamed.
2. If NOTION_PARTICIPATIONS_DB_ID / NOTION_PROFILE_PAGE_ID are unset, creates them under
   --parent-page and prints the IDs to put in .env and GitHub Secrets.

Share the parent page (and the Hackathons DB) with your Notion integration first.
Reads NOTION_TOKEN and NOTION_HACKATHONS_DB_ID from the environment or .env.

Usage: uv run python scripts/setup_notion.py [--parent-page <page_id>] [--dry-run]
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Run as a file (`python scripts/setup_notion.py`), sys.path holds scripts/, not the repo root
# where the (uninstalled) hackathon_sniper package lives.
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402

from hackathon_sniper.http import make_client  # noqa: E402
from hackathon_sniper.models import Profile  # noqa: E402
from hackathon_sniper.notion.client import NotionError, NotionGateway  # noqa: E402
from hackathon_sniper.notion.mapping import (  # noqa: E402
    HACKATHON_SCHEMA,
    NAME,
    PROFILE_SCHEMA,
    participation_schema,
    profile_to_properties,
)

PARTICIPATIONS_TITLE = "My Participations"
PROFILE_TITLE = "Hackathon Profile"
# A Notion id: 32 hex digits, optionally in dashed 8-4-4-4-12 form, at the end of the value.
_PAGE_ID = re.compile(
    r"(?:^|[^0-9a-f])([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)


def parse_page_id(value: str, label: str = "--parent-page") -> str:
    """A raw/dashed Notion id or a pasted Notion URL, normalised to 32 lowercase hex digits.

    `label` names the flag or env var in the error message.
    """
    text = value.strip().lower()
    if text.startswith(("http://", "https://")):
        text = urlsplit(text).path.rstrip("/").rsplit("/", 1)[-1]
    elif "/" in text:
        text = ""
    match = _PAGE_ID.search(text)
    if not match or (match.start(1) > 0 and text[match.start(1) - 1] != "-"):
        raise SystemExit(f"{label} must be a Notion id (32 hex characters) or its Notion URL.")
    return match.group(1).replace("-", "")


def plan_column_changes(existing: dict[str, Any]) -> dict[str, Any]:
    """Columns to add (and a title rename) for the Hackathons DB; SystemExit on a conflict."""
    changes: dict[str, Any] = {}
    title = next((name for name, prop in existing.items() if prop.get("type") == "title"), None)
    if title is None:
        raise SystemExit("Hackathons DB has no title column; is NOTION_HACKATHONS_DB_ID right?")
    if title != NAME:
        if NAME in existing:
            raise SystemExit(
                f"Cannot rename title column '{title}' to '{NAME}': a '{NAME}' column already "
                "exists. Rename or delete it in Notion, then re-run."
            )
        changes[title] = {"name": NAME}
    for name, spec in HACKATHON_SCHEMA.items():
        if name == NAME:
            continue
        expected = next(iter(spec))
        if name not in existing:
            changes[name] = spec
        elif existing[name].get("type") != expected:
            raise SystemExit(
                f"Column '{name}' exists with type {existing[name].get('type')}, expected "
                f"{expected}. Rename or delete it in Notion, then re-run."
            )
    return changes


def _env(name: str, required: bool = True) -> str:
    """Env value with surrounding whitespace stripped (pasted IDs often end in a newline)."""
    value = (os.environ.get(name) or "").strip()
    if required and not value:
        raise SystemExit(f"Missing required environment variable {name} (set it in .env).")
    return value


def _env_id(name: str, required: bool = True) -> str:
    """A Notion id from the environment (raw, dashed or URL), normalised; '' if optional+unset."""
    value = _env(name, required)
    return parse_page_id(value, name) if value else ""


class _Recovery:
    """What this run has created, so a failure part-way can say how to resume safely."""

    def __init__(self) -> None:
        self.attempted_create = False
        self.env_lines: list[str] = []
        self.orphan_profile_db: str | None = None

    def report(self) -> None:
        if not self.attempted_create:
            return
        lines = ["", "Setup stopped part-way. Before re-running:"]
        for line in self.env_lines:
            lines.append(f"  - {line}  <- add this to .env / GitHub Secrets before re-running")
        if self.orphan_profile_db:
            lines.append(
                f"  - '{PROFILE_TITLE}' database {self.orphan_profile_db} was created without "
                "its page: add a row to it manually (then set NOTION_PROFILE_PAGE_ID to that "
                "row's id), or delete it in Notion and re-run"
            )
        lines.append(
            "  - A create that timed out may still have succeeded: check the parent page for a "
            "stray database and delete it before re-running."
        )
        print("\n".join(lines), file=sys.stderr)


def _describe(changes: dict[str, Any]) -> str:
    return ", ".join(
        f"rename '{name}' -> '{spec['name']}'" if "name" in spec else f"add '{name}'"
        for name, spec in changes.items()
    )


def _run(args: argparse.Namespace) -> None:
    token = _env("NOTION_TOKEN")
    hackathons_db = _env_id("NOTION_HACKATHONS_DB_ID")
    need_participations = not _env_id("NOTION_PARTICIPATIONS_DB_ID", required=False)
    need_profile = not _env_id("NOTION_PROFILE_PAGE_ID", required=False)
    parent = (args.parent_page or "").strip()
    # Check up front so a missing flag never leaves a half-finished setup behind.
    if (need_participations or need_profile) and not parent:
        raise SystemExit(
            "--parent-page is required to create the Participations DB / Profile "
            "(or set NOTION_PARTICIPATIONS_DB_ID and NOTION_PROFILE_PAGE_ID)."
        )
    if need_participations or need_profile:
        parent = parse_page_id(parent)

    recovery = _Recovery()
    try:
        _apply(args, token, hackathons_db, parent, need_participations, need_profile, recovery)
    except NotionError:
        recovery.report()
        raise
    if args.dry_run:
        print("Dry run: no changes were made.")


def _apply(
    args: argparse.Namespace,
    token: str,
    hackathons_db: str,
    parent: str,
    need_participations: bool,
    need_profile: bool,
    recovery: _Recovery,
) -> None:
    prefix = "[dry run] would " if args.dry_run else ""
    with make_client() as client:
        notion = NotionGateway(token, client)
        properties = notion.get_database(hackathons_db).get("properties")
        if not isinstance(properties, dict):
            raise NotionError("Hackathons DB response has no properties")
        changes = plan_column_changes(properties)
        if not changes:
            print("Hackathons DB: already up to date")
        else:
            if not args.dry_run:
                notion.update_database(hackathons_db, changes)
            print(f"Hackathons DB: {prefix}{_describe(changes)}")

        if need_participations:
            if args.dry_run:
                print(f"{prefix}create database '{PARTICIPATIONS_TITLE}' under page {parent}")
            else:
                schema = participation_schema(hackathons_db)
                recovery.attempted_create = True
                db = notion.create_database(parent, PARTICIPATIONS_TITLE, schema)
                recovery.env_lines.append(f"NOTION_PARTICIPATIONS_DB_ID={db}")
                print(f"NOTION_PARTICIPATIONS_DB_ID={db}")

        if need_profile:
            if args.dry_run:
                print(
                    f"{prefix}create database '{PROFILE_TITLE}' under page {parent} with one page"
                )
            else:
                recovery.attempted_create = True
                db = notion.create_database(parent, PROFILE_TITLE, PROFILE_SCHEMA)
                recovery.orphan_profile_db = db
                print(f"Created '{PROFILE_TITLE}' database {db}")
                page = notion.create_page(db, profile_to_properties(Profile()))
                recovery.orphan_profile_db = None
                print(
                    f"NOTION_PROFILE_PAGE_ID={page}  "
                    "(fill in its fields; write free-text notes in the page body)"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--parent-page", help="Notion page ID that will contain the new databases")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the planned changes without writing"
    )
    args = parser.parse_args(argv)
    load_dotenv()
    try:
        _run(args)
    except NotionError as e:  # messages are already token-scrubbed by the gateway
        raise SystemExit(f"Notion error: {e}") from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
