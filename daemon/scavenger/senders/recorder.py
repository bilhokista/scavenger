import json

from scavenger.senders import SendResult


class RecordingSender:
    # Dry-run stand-in: writes what would be sent, sends nothing.
    # The locator carries no proof keys, so verifiers stay pending.
    kind = "recorder"

    def __init__(self, missions_dir, clock) -> None:
        from pathlib import Path

        self._missions_dir = Path(missions_dir)
        self._clock = clock

    def send(self, item) -> SendResult:
        line = json.dumps(
            {
                "at": self._clock.now().isoformat(),
                "kind": item.kind,
                "target": item.target,
                "body": item.body,
            }
        )
        path = self._missions_dir / item.mission / "dry-run.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return SendResult(ok=True, locator={"dry_run": True}, detail="recorded")
