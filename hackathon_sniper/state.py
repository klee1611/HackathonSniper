import operator
from typing import Annotated, TypedDict

from pydantic import BaseModel

from hackathon_sniper.models import HackathonEvent, Participation, Profile


class SyncResult(BaseModel):
    created: int = 0
    updated: int = 0
    archived: int = 0
    unchanged: int = 0
    failed: int = 0


class GraphState(TypedDict, total=False):
    profile: Profile
    history: list[Participation]
    existing: dict[str, HackathonEvent]  # canonical_url -> row already in Notion
    queries: list[str]
    candidates: Annotated[list[HackathonEvent], operator.add]  # parallel sources append
    to_verify: list[HackathonEvent]
    passthrough: list[HackathonEvent]
    verified: Annotated[list[HackathonEvent], operator.add]  # parallel verify branches append
    ranked: list[HackathonEvent]
    sync_result: SyncResult
    errors: Annotated[list[str], operator.add]
    source_failures: Annotated[list[str], operator.add]


class VerifyTask(TypedDict):
    """Payload sent to each parallel `verify` branch via LangGraph's Send."""

    event: HackathonEvent
