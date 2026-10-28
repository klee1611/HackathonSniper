import pytest

from hackathon_sniper.notion.client import NotionError
from tests.factories import make_deps
from tests.fakes import FakeNotion


def test_fake_notion_update_merges_properties_like_notion_patch() -> None:
    notion = FakeNotion()
    pid = notion.add_page("db", {"A": {"number": 1}, "Mine": {"checkbox": True}})
    notion.update_page(pid, {"A": {"number": 2}})
    assert notion.get_page(pid)["properties"] == {"A": {"number": 2}, "Mine": {"checkbox": True}}
    assert notion.writes == [("update", pid)]


def test_make_deps_seeds_a_profile_page() -> None:
    deps = make_deps()
    assert deps.notion.get_page("profile")["id"] == "profile"


def test_fake_notion_reads_are_deep_copies_and_creates_are_recorded() -> None:
    notion = FakeNotion()
    pid = notion.create_page("db", {"A": {"number": 1}})
    notion.query_all("db")[0]["properties"]["A"]["number"] = 99
    notion.get_page(pid)["properties"]["A"]["number"] = 99
    assert notion.get_page(pid)["properties"] == {"A": {"number": 1}}
    notion.add_database("other")
    assert notion.query_all("other") == []
    assert notion.writes == [("create", pid)]


def test_fake_notion_unknown_database_raises() -> None:
    with pytest.raises(NotionError):
        FakeNotion().query_all("nope")


def test_fake_notion_add_database_registers_empty_db() -> None:
    notion = FakeNotion()
    notion.add_database("db")
    assert notion.query_all("db") == []


def test_make_deps_registers_settings_databases() -> None:
    deps = make_deps()
    assert deps.notion.query_all(deps.settings.notion_hackathons_db_id) == []
    assert deps.notion.query_all(deps.settings.notion_participations_db_id) == []


def test_fake_notion_unknown_page_raises() -> None:
    notion = FakeNotion()
    with pytest.raises(NotionError):
        notion.get_page("missing")
    with pytest.raises(NotionError):
        notion.update_page("missing", {"A": {"number": 1}})
    with pytest.raises(NotionError):
        notion.get_page_text("missing")
    assert notion.writes == []


def test_fake_notion_generated_ids_never_overwrite_seeded_pages() -> None:
    notion = FakeNotion()
    seeded = notion.add_page("db", {"Mine": {"checkbox": True}}, page_id="page-2")
    a = notion.create_page("db", {"A": {"number": 1}})
    b = notion.create_page("db", {"A": {"number": 2}})
    assert len({seeded, a, b}) == 3
    assert len(notion.query_all("db")) == 3
    assert notion.get_page(seeded)["properties"] == {"Mine": {"checkbox": True}}
