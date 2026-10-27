import re
from datetime import date
from enum import Enum, IntEnum, StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, field_validator

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


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _lenient_enum[E: Enum](enum_cls: type[E], unknown: E) -> BeforeValidator:
    """LLMs say 'in person' or 'ONLINE'; map loosely instead of failing validation."""

    def parse(value: Any) -> Any:
        if isinstance(value, enum_cls):
            return value
        if value is None:
            return unknown
        key = _letters(str(value))
        for member in enum_cls:
            if key in (_letters(str(member.value)), _letters(member.name)):
                return member
        return unknown

    return BeforeValidator(parse)


def _iso_date_or_none(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return value


def _int_or_none(value: Any) -> Any:
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        return int(digits) if digits else None
    return value


LenientFormat = Annotated[Format, _lenient_enum(Format, Format.UNKNOWN)]
LenientSolo = Annotated[Solo, _lenient_enum(Solo, Solo.UNKNOWN)]
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
