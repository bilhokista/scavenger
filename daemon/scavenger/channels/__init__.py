from dataclasses import dataclass
from typing import Literal, Protocol

# Candidate and RungPlan live in switcher.py (defined for task 5.1) and
# are re-exported here so channels and the loop share one definition.
from scavenger.outbox import DraftSpec
from scavenger.switcher import Candidate, RungPlan

__all__ = ["Action", "Candidate", "Channel", "RungPlan"]


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

    def liveness(self): ...

    def discover(self, goal) -> list: ...

    def next_action(self, strategy, last_check) -> Action: ...
