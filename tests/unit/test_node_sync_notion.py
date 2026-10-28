from datetime import timedelta
from typing import Any

from hackathon_sniper.models import HackathonEvent, Lifecycle
from hackathon_sniper.nodes.sync_notion import MAX_ERROR_CHARS, sync_notion
from hackathon_sniper.notion.client import NotionError
from hackathon_sniper.notion.mapping import LAST_SEEN, event_to_properties, page_to_event
from hackathon_sniper.state import SyncResult
from tests.factories import TODAY, event, make_deps
from tests.fakes import FakeNotion


def seed(
    notion: FakeNotion, url: str = "https://old.dev", **fields: Any
) -> tuple[str, HackathonEvent]:
    props = event_to_properties(event(url=url, name="Old", **fields)) | {
        "Interested ⭐": {"checkbox": True}
    }
    pid = notion.add_page("hackathons", props)
    old = page_to_event(notion.get_page(pid))
    assert old is not None
    return pid, old


def test_creates_new_and_updates_changed_rows_only_touching_agent_columns() -> None:
    notion = FakeNotion()
    pid, old = seed(notion, registration_deadline=TODAY + timedelta(days=9))
    changed = old.model_copy(update={"fit_score": 77})
    new = event(url="https://new.dev", name="New", registration_deadline=TODAY + timedelta(days=3))

    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [changed, new]}, make_deps(notion=notion)
    )

    assert out["sync_result"] == SyncResult(created=1, updated=1)
    row = notion.get_page(pid)["properties"]
    assert row["Interested ⭐"] == {"checkbox": True}
    assert row["Fit Score"] == {"number": 77}


def test_unchanged_rows_are_not_written() -> None:
    notion = FakeNotion()
    _, old = seed(notion, registration_deadline=TODAY + timedelta(days=9))
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [old]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(unchanged=1) and notion.writes == []


def test_archives_expired_rows_and_skips_expired_new_events() -> None:
    notion = FakeNotion()
    pid, old = seed(notion, registration_deadline=TODAY - timedelta(days=1))
    gone = event(
        url="https://gone.dev", name="Gone", registration_deadline=TODAY - timedelta(days=2)
    )
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [old, gone]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(archived=1, skipped=1)
    assert notion.get_page(pid)["properties"]["Lifecycle"] == {"select": {"name": "Expired"}}
    assert notion.writes == [("update", pid)]


def test_dry_run_counts_but_never_writes() -> None:
    notion = FakeNotion()
    out = sync_notion({"existing": {}, "ranked": [event()]}, make_deps(notion=notion, dry_run=True))
    assert out["sync_result"].created == 1 and notion.writes == []


def test_dry_run_counts_updates_and_archives_without_writing() -> None:
    notion = FakeNotion()
    _, old = seed(notion, registration_deadline=TODAY - timedelta(days=1))
    _, other = seed(notion, "https://other.dev", registration_deadline=TODAY + timedelta(days=5))
    changed = other.model_copy(update={"fit_score": 1})
    out = sync_notion(
        {
            "existing": {old.canonical_url: old, other.canonical_url: other},
            "ranked": [old, changed],
        },
        make_deps(notion=notion, dry_run=True),
    )
    assert out["sync_result"] == SyncResult(archived=1, updated=1) and notion.writes == []


def test_write_failures_are_counted() -> None:
    class Broken(FakeNotion):
        def create_page(self, database_id: str, properties: dict[str, Any]) -> str:
            raise RuntimeError("Notion 500")

    out = sync_notion({"existing": {}, "ranked": [event()]}, make_deps(notion=Broken()))
    assert out["sync_result"].failed == 1 and "Notion 500" in out["errors"][0]


# ---- extended deadlines ---------------------------------------------------------------------


def test_extended_deadline_reactivates_an_expired_row() -> None:
    notion = FakeNotion()
    pid, old = seed(
        notion, registration_deadline=TODAY - timedelta(days=3), lifecycle=Lifecycle.EXPIRED
    )
    extended = old.model_copy(update={"registration_deadline": TODAY + timedelta(days=10)})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [extended]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(updated=1)
    assert notion.get_page(pid)["properties"]["Lifecycle"] == {"select": {"name": "Active"}}


def test_already_expired_row_that_changes_counts_as_updated_not_archived() -> None:
    notion = FakeNotion()
    _, old = seed(
        notion, registration_deadline=TODAY - timedelta(days=3), lifecycle=Lifecycle.EXPIRED
    )
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [old.model_copy(update={"prize_usd": 5})]},
        make_deps(notion=notion),
    )
    assert out["sync_result"] == SyncResult(updated=1)


# ---- write-budget rule (Last Seen) ----------------------------------------------------------


def test_only_last_seen_changed_and_fresh_is_not_written() -> None:
    notion = FakeNotion()
    _, old = seed(
        notion,
        registration_deadline=TODAY + timedelta(days=9),
        last_seen=TODAY - timedelta(days=6),
    )
    seen = old.model_copy(update={"last_seen": TODAY})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [seen]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(unchanged=1) and notion.writes == []


def test_only_last_seen_changed_and_stale_is_written() -> None:
    notion = FakeNotion()
    pid, old = seed(
        notion,
        registration_deadline=TODAY + timedelta(days=9),
        last_seen=TODAY - timedelta(days=7),
    )
    seen = old.model_copy(update={"last_seen": TODAY})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [seen]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(updated=1)
    assert notion.get_page(pid)["properties"][LAST_SEEN] == {
        "date": {"start": TODAY.isoformat(), "end": None}
    }


def test_only_last_seen_changed_and_stored_missing_is_written() -> None:
    notion = FakeNotion()
    pid, old = seed(notion, registration_deadline=TODAY + timedelta(days=9))
    seen = old.model_copy(update={"last_seen": TODAY})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [seen]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(updated=1) and notion.writes == [("update", pid)]


def test_real_change_with_fresh_last_seen_is_written_including_last_seen() -> None:
    notion = FakeNotion()
    pid, old = seed(
        notion,
        registration_deadline=TODAY + timedelta(days=9),
        last_seen=TODAY - timedelta(days=1),
    )
    changed = old.model_copy(update={"last_seen": TODAY, "prize_usd": 1000})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [changed]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(updated=1)
    row = notion.get_page(pid)["properties"]
    assert row["Prize USD"] == {"number": 1000}
    assert row[LAST_SEEN] == {"date": {"start": TODAY.isoformat(), "end": None}}


# ---- duplicate-row prevention ---------------------------------------------------------------


def test_event_without_page_id_but_known_url_updates_existing_row() -> None:
    notion = FakeNotion()
    pid, old = seed(notion, registration_deadline=TODAY + timedelta(days=9))
    fresh = old.model_copy(update={"notion_page_id": None, "fit_score": 50})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [fresh]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(updated=1)
    assert notion.writes == [("update", pid)]


def test_event_with_conflicting_page_id_updates_existing_row() -> None:
    notion = FakeNotion()
    pid, old = seed(notion, registration_deadline=TODAY + timedelta(days=9))
    other = old.model_copy(update={"notion_page_id": "some-other-page", "fit_score": 50})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [other]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(updated=1)
    assert notion.writes == [("update", pid)]


def test_same_url_twice_in_ranked_creates_one_row() -> None:
    notion = FakeNotion()
    first = event(url="https://dup.dev", name="Dup", fit_score=10)
    second = event(url="https://dup.dev/", name="Dup again", fit_score=20)
    out = sync_notion({"existing": {}, "ranked": [first, second]}, make_deps(notion=notion))
    assert out["sync_result"] == SyncResult(created=1, skipped=1)
    assert [kind for kind, _ in notion.writes] == ["create"]


# ---- determinism and error hygiene ----------------------------------------------------------


def test_rows_are_written_in_url_order() -> None:
    notion = FakeNotion()
    ranked = [event(url=f"https://{name}.dev", name=name) for name in ("c", "a", "b")]
    sync_notion({"existing": {}, "ranked": ranked}, make_deps(notion=notion))
    urls = [notion.get_page(pid)["properties"]["URL"]["url"] for _, pid in notion.writes]
    assert urls == ["https://a.dev", "https://b.dev", "https://c.dev"]


def test_a_failed_row_does_not_stop_the_others() -> None:
    notion = FakeNotion()
    broken = event(url="https://a.dev", name="A", notion_page_id="deleted-page", fit_score=1)
    fine = event(url="https://b.dev", name="B")
    out = sync_notion({"existing": {}, "ranked": [fine, broken]}, make_deps(notion=notion))
    assert out["sync_result"] == SyncResult(created=1, failed=1)
    assert out["errors"] == [out["errors"][0]]
    assert out["errors"][0].startswith(f"sync https://a.dev: {NotionError.__name__}: ")
    assert [kind for kind, _ in notion.writes] == ["create"]


def test_error_messages_are_truncated() -> None:
    class Broken(FakeNotion):
        def create_page(self, database_id: str, properties: dict[str, Any]) -> str:
            raise RuntimeError("x" * 5000)

    out = sync_notion({"existing": {}, "ranked": [event()]}, make_deps(notion=Broken()))
    assert len(out["errors"][0]) <= MAX_ERROR_CHARS
    assert out["errors"][0].startswith("sync https://a.devpost.com: RuntimeError: xxx")


def test_empty_state_is_a_no_op() -> None:
    notion = FakeNotion()
    out = sync_notion({}, make_deps(notion=notion))
    assert out == {"sync_result": SyncResult(), "errors": []} and notion.writes == []


# ---- skipped counter ------------------------------------------------------------------------


def test_every_ranked_entry_is_counted_exactly_once() -> None:
    class Flaky(FakeNotion):
        def create_page(self, database_id: str, properties: dict[str, Any]) -> str:
            if properties["URL"]["url"] == "https://fail.dev":
                raise RuntimeError("Notion 500")
            return super().create_page(database_id, properties)

    notion = Flaky()
    _, kept = seed(notion, "https://kept.dev", registration_deadline=TODAY + timedelta(days=9))
    _, changed = seed(notion, "https://changed.dev", registration_deadline=TODAY + timedelta(9))
    _, closing = seed(notion, "https://closing.dev", registration_deadline=TODAY - timedelta(1))
    soon = TODAY + timedelta(days=4)
    ranked = [
        kept,
        changed.model_copy(update={"fit_score": 60}),
        closing,
        event(url="https://new.dev", registration_deadline=soon),
        event(url="https://new.dev/", registration_deadline=soon),  # duplicate URL
        event(url="https://closed.dev", registration_deadline=TODAY - timedelta(days=1)),
        event(url="https://fail.dev", registration_deadline=soon),
    ]
    existing = {e.canonical_url: e for e in (kept, changed, closing)}
    out = sync_notion({"existing": existing, "ranked": ranked}, make_deps(notion=notion))
    r = out["sync_result"]
    assert r == SyncResult(created=1, updated=1, archived=1, unchanged=1, failed=1, skipped=2)
    total = r.created + r.updated + r.archived + r.unchanged + r.failed + r.skipped
    assert total == len(ranked)


# ---- manual dismissal -----------------------------------------------------------------------


def test_manually_expired_row_with_same_deadline_stays_expired() -> None:
    notion = FakeNotion()
    deadline = TODAY + timedelta(days=20)
    _, old = seed(notion, registration_deadline=deadline, lifecycle=Lifecycle.EXPIRED)
    again = old.model_copy(update={"lifecycle": Lifecycle.ACTIVE})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [again]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(unchanged=1) and notion.writes == []


def test_manually_expired_row_with_extended_deadline_reactivates() -> None:
    notion = FakeNotion()
    deadline = TODAY + timedelta(days=20)
    pid, old = seed(notion, registration_deadline=deadline, lifecycle=Lifecycle.EXPIRED)
    extended = old.model_copy(update={"registration_deadline": deadline + timedelta(days=5)})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [extended]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(updated=1)
    assert notion.get_page(pid)["properties"]["Lifecycle"] == {"select": {"name": "Active"}}


def test_expired_row_never_gets_a_last_seen_refresh() -> None:
    notion = FakeNotion()
    _, old = seed(
        notion,
        registration_deadline=TODAY - timedelta(days=30),
        lifecycle=Lifecycle.EXPIRED,
        last_seen=TODAY - timedelta(days=60),
    )
    seen = old.model_copy(update={"last_seen": TODAY})
    out = sync_notion(
        {"existing": {old.canonical_url: old}, "ranked": [seen]}, make_deps(notion=notion)
    )
    assert out["sync_result"] == SyncResult(unchanged=1) and notion.writes == []
