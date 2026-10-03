from scavenger.senders import SendResult


class ManualSender:
    kind = "manual"

    def send(self, item) -> SendResult:
        return SendResult(
            ok=False,
            locator={},
            detail="manual send requires scavenger mark-sent",
        )
