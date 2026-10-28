from hackathon_sniper.models import Participation, Profile
from hackathon_sniper.nodes._context import (
    MAX_HISTORY_LINE_CHARS,
    MAX_NOTES_CHARS,
    history_summary,
    profile_summary,
)


def test_profile_summary_lists_fields() -> None:
    text = profile_summary(Profile(skills=["Python"], interests=["AI"], min_prize_usd=5000))
    assert "Skills: Python" in text
    assert "Interests: AI" in text
    assert "Minimum prize: $5,000" in text
    assert "Notes: n/a" in text


def test_profile_summary_caps_huge_notes() -> None:
    text = profile_summary(Profile(notes="x" * 100_000, skills=["y" * 10_000]))
    assert len(text) < MAX_NOTES_CHARS + 1000


def test_history_summary_caps_lines_and_count() -> None:
    history = [Participation(hackathon="H", project="p" * 10_000)] * 40
    lines = history_summary(history).splitlines()
    assert len(lines) == 15
    assert all(len(line) <= MAX_HISTORY_LINE_CHARS for line in lines)


def test_history_summary_empty() -> None:
    assert history_summary([]) == "No past participations recorded."


def test_history_summary_clips_each_field_so_later_fields_survive() -> None:
    line = history_summary(
        [Participation(hackathon="H", project="p" * 10_000, tech_stack=["Rust"], outcome="Won")]
    )
    assert "stack=Rust" in line
    assert "outcome=Won" in line


def test_history_summary_one_line_per_entry() -> None:
    history = [
        Participation(hackathon="H\nack", project="multi\nline\r\nproject", outcome="a\nb"),
        Participation(hackathon="Two", tech_stack=["x\ny"]),
    ]
    lines = history_summary(history).splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("- H ack: multi line")
