from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

# Candidate and RungPlan live in switcher.py (defined for task 5.1) and
# are re-exported here so channels and the loop share one definition.
from scavenger.outbox import DraftSpec
from scavenger.switcher import Candidate, RungPlan

__all__ = [
    "Action",
    "Candidate",
    "Channel",
    "Liveness",
    "RungPlan",
    "UnknownChannel",
    "get",
    "liveness_all",
    "register",
]


@dataclass(frozen=True)
class Liveness:
    alive: bool
    evidence_urls: tuple
    detail: str
    checked_at: datetime


@dataclass(frozen=True)
class Action:
    kind: Literal["draft", "local", "wait"]
    draft: DraftSpec | None
    description: str
    fingerprint: str
    tokens_in: int = 0
    tokens_out: int = 0


class Channel(Protocol):
    name: str

    def liveness(self) -> Liveness: ...

    def discover(self, goal) -> list: ...

    def next_action(self, strategy, last_check) -> Action: ...


class UnknownChannel(Exception):
    pass


_REGISTRY: dict = {}


def register(channel: Channel) -> None:
    _REGISTRY[channel.name] = channel


def get(name: str) -> Channel:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownChannel(f"unknown channel: {name}") from None


def liveness_all(channels: dict, store=None, mission=None) -> dict:
    live = {}
    for name, channel in channels.items():
        try:
            state = channel.liveness()
        except Exception:  # noqa: BLE001 - dead probe means skip, never fatal
            state = None
        if state is not None and state.alive:
            live[name] = state
        elif store is not None:
            store.add_event(
                mission=mission,
                level="warn",
                kind="channel_skipped",
                message=f"channel {name} dead or unreachable",
            )
    return live
