from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class Notice:
    kind: Literal["approval_request", "emergency_stop", "mission_stop", "escalation"]
    title: str
    body: str
    outbox_id: str | None = None
    token: str | None = None


class Notifier(Protocol):
    name: str

    def notify(self, notice: Notice) -> None: ...
