import email.utils
import json

from scavenger.senders import SendResult


class EmailSender:
    kind = "email"

    def __init__(self, smtp_client, from_address: str) -> None:
        self._smtp = smtp_client
        self._from_address = from_address

    def send(self, item) -> SendResult:
        try:
            payload = json.loads(item.payload_json)
        except ValueError:
            payload = {}
        subject = payload.get("subject", f"scavenger {item.kind}")
        message_id = email.utils.make_msgid()
        self._smtp.send(item.target, subject, item.body, message_id)
        return SendResult(
            ok=True,
            locator={
                "thread_message_id": message_id,
                "counterparty": item.target,
            },
            detail=f"email to {item.target}",
        )
