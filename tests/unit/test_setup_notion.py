from typing import Any

import pytest

import scripts.setup_notion as setup
from hackathon_sniper.notion.client import NotionError
from hackathon_sniper.notion.mapping import HACKATHON_SCHEMA
from scripts.setup_notion import plan_column_changes

V2 = {
    "Name": {"type": "title"},
    "URL": {"type": "url"},
    "Registration Deadline": {"type": "date"},
    "Prize USD": {"type": "number"},
    "Solo Friendly": {"type": "checkbox"},
    "End Date": {"type": "date"},
    "Description/AI Focus": {"type": "rich_text"},
}

# A Hackathons DB that already has every agent-owned column.
COMPLETE = {name: {"type": next(iter(spec))} for name, spec in HACKATHON_SCHEMA.items()}

TOKEN = "secret_SHOULD_NEVER_BE_PRINTED"
HACKATHONS_DB = "hackathons-db-id"


def test_adds_only_missing_columns_and_keeps_v2_ones() -> None:
    changes = plan_column_changes(V2)
    assert "URL" not in changes and "Solo Friendly" not in changes and "Name" not in changes
    assert changes["Solo"] == {"select": {}}
    assert changes["Event Dates"] == {"date": {}}


def test_renames_title_column() -> None:
    assert plan_column_changes({"Title": {"type": "title"}})["Title"] == {"name": "Name"}


def test_type_conflict_aborts() -> None:
    with pytest.raises(SystemExit, match="Solo"):
        plan_column_changes({"Name": {"type": "title"}, "Solo": {"type": "checkbox"}})


def test_complete_schema_needs_no_changes() -> None:
    assert plan_column_changes(COMPLETE) == {}


def test_rename_blocked_by_existing_non_title_name_column() -> None:
    with pytest.raises(SystemExit, match="Name"):
        plan_column_changes({"Title": {"type": "title"}, "Name": {"type": "rich_text"}})


def test_missing_title_column_aborts() -> None:
    with pytest.raises(SystemExit, match="title"):
        plan_column_changes({"URL": {"type": "url"}})


# ---- main() with a fake gateway ---------------------------------------------------------


class FakeGateway:
    def __init__(self, properties: dict[str, Any], fail: bool = False) -> None:
        self.properties = properties
        self.fail = fail
        self.token: str | None = None
        self.writes: list[tuple[str, tuple[Any, ...]]] = []
        self.client_closed = False
        self.reads = 0

    def get_database(self, database_id: str) -> dict[str, Any]:
        self.reads += 1
        if self.fail:
            raise NotionError("GET /databases/x -> 401: unauthorized")
        assert database_id == HACKATHONS_DB
        return {"properties": self.properties}

    def update_database(self, *args: Any) -> None:
        self.writes.append(("update_database", args))

    def create_database(self, *args: Any) -> str:
        self.writes.append(("create_database", args))
        return f"db-{len(self.writes)}"

    def create_page(self, *args: Any) -> str:
        self.writes.append(("create_page", args))
        return "profile-page-id"


class FakeClient:
    def __init__(self, gateway: FakeGateway) -> None:
        self.gateway = gateway

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.gateway.client_closed = True


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Any:
    """Run main() against a fake gateway; returns (exit_code, stdout+stderr, gateway)."""
    monkeypatch.setattr(setup, "load_dotenv", lambda: None)  # never read the real .env
    for key in ("NOTION_PARTICIPATIONS_DB_ID", "NOTION_PROFILE_PAGE_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NOTION_TOKEN", f"  {TOKEN}\n")
    monkeypatch.setenv("NOTION_HACKATHONS_DB_ID", f"{HACKATHONS_DB}\n")

    def _run(
        properties: dict[str, Any], argv: list[str], fail: bool = False
    ) -> tuple[int, str, FakeGateway]:
        gateway = FakeGateway(properties, fail=fail)

        def make_gateway(token: str, client: Any) -> FakeGateway:
            gateway.token = token
            return gateway

        monkeypatch.setattr(setup, "make_client", lambda: FakeClient(gateway))
        monkeypatch.setattr(setup, "NotionGateway", make_gateway)
        try:
            code = setup.main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
            if isinstance(e.code, str):
                print(e.code)
        out = capsys.readouterr()
        return code, out.out + out.err, gateway

    return _run


def _set_existing_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_PARTICIPATIONS_DB_ID", "participations-id")
    monkeypatch.setenv("NOTION_PROFILE_PAGE_ID", "profile-id")


def test_main_up_to_date_makes_no_writes(run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_existing_ids(monkeypatch)
    code, out, gateway = run(COMPLETE, [])
    assert code == 0
    assert "already up to date" in out
    assert gateway.writes == []
    assert gateway.token == TOKEN  # whitespace stripped
    assert gateway.client_closed
    assert TOKEN not in out


def test_main_adds_missing_columns(run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_existing_ids(monkeypatch)
    code, out, gateway = run(V2, [])
    assert code == 0
    assert len(gateway.writes) == 1
    method, (db_id, changes) = gateway.writes[0]
    assert method == "update_database" and db_id == HACKATHONS_DB
    assert "Solo" in changes and "URL" not in changes
    assert "Solo" in out and TOKEN not in out


def test_main_creates_participations_and_profile(run: Any) -> None:
    code, out, gateway = run(COMPLETE, ["--parent-page", RAW_ID])
    assert code == 0
    methods = [m for m, _ in gateway.writes]
    assert methods == ["create_database", "create_database", "create_page"]
    participations = gateway.writes[0][1]
    assert participations[0] == RAW_ID and participations[1] == "My Participations"
    relation = participations[2]["Hackathon"]["relation"]
    assert relation["database_id"] == HACKATHONS_DB
    assert gateway.writes[1][1][:2] == (RAW_ID, "Hackathon Profile")
    assert "NOTION_PARTICIPATIONS_DB_ID=db-1" in out
    assert "NOTION_PROFILE_PAGE_ID=profile-page-id" in out
    assert TOKEN not in out


def test_main_missing_parent_page_errors_before_any_write(run: Any) -> None:
    code, out, gateway = run(V2, [])
    assert code != 0
    assert "--parent-page" in out
    assert gateway.writes == []  # column changes are not applied either
    assert "Traceback" not in out and TOKEN not in out


def test_main_dry_run_makes_no_writes(run: Any) -> None:
    code, out, gateway = run({"Title": {"type": "title"}}, ["--dry-run", "--parent-page", RAW_ID])
    assert code == 0
    assert gateway.writes == []
    assert "Solo" in out and "Title" in out
    assert "My Participations" in out and "Hackathon Profile" in out
    assert "dry run" in out.lower()


def test_main_notion_error_is_clean(run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_existing_ids(monkeypatch)
    code, out, gateway = run(COMPLETE, [], fail=True)
    assert code != 0
    assert "401" in out
    assert "Traceback" not in out and TOKEN not in out
    assert gateway.writes == []
    assert gateway.client_closed


def test_main_type_conflict_is_clean(run: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_existing_ids(monkeypatch)
    code, out, gateway = run({"Name": {"type": "title"}, "Solo": {"type": "checkbox"}}, [])
    assert code != 0 and "Solo" in out
    assert gateway.writes == []


@pytest.mark.parametrize("missing", ["NOTION_TOKEN", "NOTION_HACKATHONS_DB_ID"])
def test_main_missing_env_is_clean(run: Any, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv(missing, "  \n")  # blank after stripping counts as missing
    code, out, gateway = run(COMPLETE, [])
    assert code != 0
    assert missing in out
    assert "Traceback" not in out and TOKEN not in out
    assert gateway.writes == []


RAW_ID = "0123456789abcdef0123456789abcdef"
DASHED_ID = "01234567-89ab-cdef-0123-456789abcdef"


@pytest.mark.parametrize(
    "value",
    [
        RAW_ID,
        DASHED_ID,
        f"https://www.notion.so/My-Page-{RAW_ID}",
        f"https://www.notion.so/workspace/My-Page-{RAW_ID}?pvs=4",
        f"  {RAW_ID.upper()}\n",
    ],
)
def test_parse_page_id_accepts_ids_and_urls(value: str) -> None:
    assert setup.parse_page_id(value) == RAW_ID


@pytest.mark.parametrize(
    "value",
    ["not-an-id", "", RAW_ID[:-1], RAW_ID + "0", "https://www.notion.so/My-Page", "../" + RAW_ID],
)
def test_parse_page_id_rejects_garbage(value: str) -> None:
    with pytest.raises(SystemExit, match="parent-page"):
        setup.parse_page_id(value)


@pytest.mark.parametrize("value", [RAW_ID, DASHED_ID, f"https://www.notion.so/My-Page-{RAW_ID}"])
def test_main_uses_normalized_parent_page(run: Any, value: str) -> None:
    code, out, gateway = run(COMPLETE, ["--parent-page", value])
    assert code == 0
    assert [args[0] for m, args in gateway.writes if m == "create_database"] == [RAW_ID, RAW_ID]


def test_main_invalid_parent_page_fails_before_any_call(run: Any) -> None:
    code, out, gateway = run(V2, ["--parent-page", "not-an-id"])
    assert code == 1
    assert "--parent-page" in out
    assert gateway.reads == 0 and gateway.writes == []
    assert "Traceback" not in out and TOKEN not in out


def test_main_parent_page_not_validated_when_not_needed(
    run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_existing_ids(monkeypatch)
    code, out, gateway = run(COMPLETE, ["--parent-page", "not-an-id"])
    assert code == 0 and "already up to date" in out
