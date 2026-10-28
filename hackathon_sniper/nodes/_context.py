from hackathon_sniper.models import Participation, Profile

# Profile and history are user-authored Notion text that goes into LLM prompts.
# Cap them so one oversized page cannot blow the model context or the token budget.
MAX_FIELD_CHARS = 300
MAX_NOTES_CHARS = 1000
MAX_HISTORY_FIELD_CHARS = 100
MAX_HISTORY_LINE_CHARS = 300


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _one_line(text: str, limit: int = MAX_HISTORY_FIELD_CHARS) -> str:
    """Collapse all whitespace (newlines included) to single spaces, then clip."""
    return _clip(" ".join(text.split()), limit)


def _join(items: list[str], empty: str) -> str:
    return _clip(", ".join(items), MAX_FIELD_CHARS) or empty


def profile_summary(profile: Profile) -> str:
    prize = f"${profile.min_prize_usd:,}" if profile.min_prize_usd else "none"
    location = _clip(profile.location, MAX_FIELD_CHARS) if profile.location else "n/a"
    return "\n".join(
        [
            f"Skills: {_join(profile.skills, 'n/a')}",
            f"Interests: {_join(profile.interests, 'n/a')}",
            f"Preferred formats: {_join([f.value for f in profile.formats], 'any')}",
            f"Team preference: {profile.team.value}",
            f"Minimum prize: {prize}",
            f"Location: {location}",
            f"Notes: {_clip(profile.notes, MAX_NOTES_CHARS) or 'n/a'}",
        ]
    )


def history_summary(history: list[Participation], limit: int = 15) -> str:
    if not history:
        return "No past participations recorded."
    return "\n".join(
        _clip(
            f"- {_one_line(h.hackathon or '') or 'Unknown event'}: "
            f"{_one_line(h.project) or 'project'}; "
            f"stack={_one_line(', '.join(h.tech_stack)) or 'n/a'}; "
            f"outcome={_one_line(h.outcome or '') or 'n/a'}",
            MAX_HISTORY_LINE_CHARS,
        )
        for h in history[:limit]
    )
