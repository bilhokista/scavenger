import json
from dataclasses import dataclass
from typing import Protocol

from scavenger import clock
from scavenger.outbox import DraftSpec, check_limits


@dataclass(frozen=True)
class SendResult:
    ok: bool
    locator: dict
    detail: str


class Sender(Protocol):
    kind: str

    def send(self, item) -> SendResult: ...


class SenderError(Exception):
    pass


def send_approved(store, senders: dict, outbox) -> None:
    # Decision: plan names the third arg outbox_limits, but rechecking
    # limits needs config thresholds plus the github client, both carried
    # by the Outbox. Passing it keeps one seam instead of three.
    for item in store.list_outbox("approved"):
        if item.kind == "manual":
            continue
        sender = senders.get(item.kind)
        if sender is None:
            store.add_event(
                mission=item.mission,
                level="warn",
                kind="no_sender",
                message=f"no sender for kind {item.kind}",
            )
            continue
        strategy = (
            store.get_strategy(item.strategy_id)
            if item.strategy_id is not None
            else None
        )
        spec = DraftSpec(
            kind=item.kind,
            target=item.target,
            body=item.body,
            links_json=item.links_json,
            payload_json=item.payload_json,
            cost=item.cost,
            guarantor=strategy.guarantor if strategy else "none",
            guarantor_evidence=(strategy.guarantor_evidence if strategy else None),
        )
        github = getattr(senders.get("github_pr"), "github", None)
        violations = check_limits(spec, store, github, outbox._config.outbox)
        if violations:
            store.set_outbox_status(
                item.id,
                "rejected",
                decided_at=clock.now().isoformat(),
                decided_via="limit",
            )
            for violation in violations:
                store.add_event(
                    mission=item.mission,
                    level="warn",
                    kind="limit",
                    message=f"{violation.rule}: {violation.detail}",
                )
            continue
        try:
            result = sender.send(item)
        except Exception as error:  # noqa: BLE001 - sender crash is failed, not fatal
            result = SendResult(ok=False, locator={}, detail=str(error))
        if not result.ok:
            store.set_outbox_status(
                item.id,
                "failed",
                sent_at=clock.now().isoformat(),
                send_result_json=json.dumps({"detail": result.detail}),
            )
            continue
        store.set_outbox_status(
            item.id,
            "sent",
            sent_at=clock.now().isoformat(),
            send_result_json=json.dumps(result.locator),
        )
        round_row = store.round_for_outbox(item.id)
        if round_row is not None:
            store.set_round_locator(
                round_row.id, json.dumps(result.locator, sort_keys=True)
            )
            if round_row.ended_at is None:
                store.end_round(
                    round_row.id,
                    ended_at=clock.now().isoformat(),
                    result_state=round_row.result_state or "pending",
                )
