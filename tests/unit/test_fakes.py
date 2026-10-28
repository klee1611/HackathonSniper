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
    assert notion.query_all("other") == []
    assert notion.writes == [("create", pid)]
