from dataclasses import dataclass, replace
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
    supports_replies: bool

    def notify(self, notice: Notice) -> None: ...


class NotifyError(Exception):
    pass


class AllNotifiersFailed(Exception):
    pass


def fan_out(notice: Notice, notifiers, store=None, mission=None) -> None:
    targets = list(notifiers)
    failures = 0
    delivered = 0
    for notifier in targets:
        outgoing = notice
        if not notifier.supports_replies:
            lines = [notice.body]
            if notice.outbox_id:
                lines.append(f"Approve locally: scavenger approve {notice.outbox_id}")
            outgoing = replace(notice, body="\n".join(lines), token=None)
        try:
            notifier.notify(outgoing)
            delivered += 1
        except Exception:  # noqa: BLE001 - one notifier must never stop the rest
            failures += 1
            if store is not None:
                store.add_event(
                    mission=mission,
                    level="error",
                    kind="notify_failed",
                    message=f"notifier {notifier.name} failed",
                )
    if delivered == 0 and targets:
        raise AllNotifiersFailed("every notifier failed")
