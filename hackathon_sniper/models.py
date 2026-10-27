import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum, IntEnum, StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, field_validator

from hackathon_sniper.dates import parse_date
from hackathon_sniper.urls import canonicalize


class Format(StrEnum):
    ONLINE = "Online"
    IN_PERSON = "In-person"
    HYBRID = "Hybrid"
    UNKNOWN = "Unknown"


class Solo(StrEnum):
    YES = "Yes"
    NO = "No"
    UNKNOWN = "Unknown"


class Verification(StrEnum):
    VERIFIED = "Verified"
    PARTIAL = "Partial"
    UNVERIFIED = "Unverified"


class Lifecycle(StrEnum):
    ACTIVE = "Active"
    EXPIRED = "Expired"


class TeamPreference(StrEnum):
    SOLO_ONLY = "Solo only"
    TEAM_ONLY = "Team only"
    EITHER = "Either"


class Origin(IntEnum):
    """How much we trust an event's data. Higher wins when merging."""

    SNIPPET = 1
    SOURCE = 2
    PAGE = 3


_NUMBER = re.compile(r"(-?)(\d[\d,]*(?:\.\d+)?)\s*([kKmM](?![A-Za-z]))?")
_MULTIPLIERS = {"k": 1_000, "m": 1_000_000}


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _lenient_enum[E: Enum](
    enum_cls: type[E], unknown: E, aliases: Mapping[str, E] | None = None
) -> BeforeValidator:
    """LLMs say 'in person' or 'ONLINE'; map loosely instead of failing validation.

    Alias keys are compared after normalizing to lowercase letters, so 'True' and
    True both hit the "true" alias.
    """

    def parse(value: Any) -> Any:
        if isinstance(value, enum_cls):
            return value
        if value is None:
            return unknown
        key = _letters(str(value))
        for member in enum_cls:
            if key in (_letters(str(member.value)), _letters(member.name)):
                return member
        return (aliases or {}).get(key, unknown)

    return BeforeValidator(parse)


def _iso_date_or_none(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return parse_date(value)
    return None


def _int_or_none(value: Any) -> Any:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value >= 0 else None
    if isinstance(value, str):
        match = _NUMBER.search(value)
        if not match or match.group(1):
            return None
        number = float(match.group(2).replace(",", ""))
        suffix = match.group(3)
        return int(number * _MULTIPLIERS[suffix.lower()]) if suffix else int(number)
    return value


_FORMAT_ALIASES = {
    "virtual": Format.ONLINE,
    "remote": Format.ONLINE,
    "onsite": Format.IN_PERSON,
    "offline": Format.IN_PERSON,
    "inperson": Format.IN_PERSON,
}
_SOLO_ALIASES = {
    "true": Solo.YES,
    "y": Solo.YES,
    "allowed": Solo.YES,
    "false": Solo.NO,
    "n": Solo.NO,
}

LenientFormat = Annotated[Format, _lenient_enum(Format, Format.UNKNOWN, _FORMAT_ALIASES)]
LenientSolo = Annotated[Solo, _lenient_enum(Solo, Solo.UNKNOWN, _SOLO_ALIASES)]
LenientDate = Annotated[date | None, BeforeValidator(_iso_date_or_none)]
LenientInt = Annotated[int | None, BeforeValidator(_int_or_none)]


class HackathonEvent(BaseModel):
    canonical_url: str
    name: str
    sources: list[str] = Field(default_factory=list)
    registration_deadline: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    prize_usd: int | None = None
    format: Format = Format.UNKNOWN
    location: str | None = None
    solo_allowed: Solo = Solo.UNKNOWN
    themes: list[str] = Field(default_factory=list)
    description: str = ""
    origin: Origin = Origin.SNIPPET
    verification: Verification = Verification.UNVERIFIED
    verified_at: date | None = None
    last_seen: date | None = None
    fit_score: int | None = None
    fit_reason: str | None = None
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    notion_page_id: str | None = None

    @field_validator("canonical_url", mode="before")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return canonicalize(value)

    def closing_date(self) -> date | None:
        return self.registration_deadline or self.end_date

    def is_expired(self, today: date) -> bool:
        closing = self.closing_date()
        return closing is not None and closing < today


class Profile(BaseModel):
    skills: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    formats: list[Format] = Field(default_factory=list)
    team: TeamPreference = TeamPreference.EITHER
    min_prize_usd: int | None = None
    location: str | None = None
    notes: str = ""


class Participation(BaseModel):
    hackathon: str | None = None
    project: str = ""
    role: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    outcome: str | None = None
    notes: str = ""
