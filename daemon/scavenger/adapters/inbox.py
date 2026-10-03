import email
import email.utils
import imaplib
import re
from dataclasses import dataclass
from datetime import datetime

from scavenger.adapters import Adapter, ProofResult, ProofState
from scavenger.llm import LLMError, complete_json

_QUOTED_ON_RE = re.compile(r"^On .* wrote:$")


@dataclass(frozen=True)
class ImapMessage:
    message_id: str
    from_addr: str
    date: datetime
    in_reply_to: str
    references: str
    subject: str
    text: str


class ImapClient:
    def __init__(
        self, host: str, username: str, password: str, folder: str = "INBOX"
    ) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._folder = folder

    def list_since(self, since: datetime) -> list:
        try:
            mail = imaplib.IMAP4_SSL(self._host)
            mail.login(self._username, self._password)
            mail.select(self._folder, readonly=True)
            stamp = since.strftime("%d-%b-%Y")
            _, data = mail.search(None, f"(SINCE {stamp})")
            messages = []
            for number in data[0].split():
                _, fetched = mail.fetch(
                    number,
                    "(BODY.PEEK[HEADER.FIELDS"
                    " (MESSAGE-ID FROM DATE IN-REPLY-TO REFERENCES SUBJECT)]"
                    " BODY.PEEK[TEXT])",
                )
                messages.append(self._parse(fetched))
            mail.close()
            mail.logout()
        except (imaplib.IMAP4.error, OSError) as error:
            raise ConnectionError(f"imap failed: {error}") from error
        return [message for message in messages if message.date >= since]

    @staticmethod
    def _parse(fetched: list) -> ImapMessage:
        header = b""
        text = ""
        for part in fetched:
            if isinstance(part, tuple):
                if b"MESSAGE-ID" in part[0].upper():
                    header = part[1]
                else:
                    text += part[1].decode("utf-8", errors="replace")
        parsed = email.message_from_bytes(header)
        date = email.utils.parsedate_to_datetime(parsed.get("Date", ""))
        return ImapMessage(
            message_id=parsed.get("Message-ID", ""),
            from_addr=email.utils.parseaddr(parsed.get("From", ""))[1],
            date=date,
            in_reply_to=parsed.get("In-Reply-To", ""),
            references=parsed.get("References", ""),
            subject=parsed.get("Subject", ""),
            text=text.strip(),
        )


def strip_quoted(text: str) -> str:
    kept = []
    for line in text.splitlines():
        if line.startswith(">") or _QUOTED_ON_RE.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


class InboxAdapter(Adapter):
    name = "inbox"

    def __init__(self, imap, llm) -> None:
        self._imap = imap
        self._llm = llm

    def check(self, rung: str, locator: dict, since: datetime) -> ProofResult:
        thread_id = locator["thread_message_id"]
        counterparty = locator["counterparty"].lower()
        require = locator.get("require", "any_reply")
        try:
            messages = self._imap.list_since(since)
        except (ConnectionError, OSError):
            return ProofResult(
                state=ProofState.UNVERIFIED,
                evidence_raw="",
                detail="imap error",
            )
        for message in messages:
            if message.from_addr.lower() != counterparty:
                continue
            if message.date <= since:
                continue
            thread = f"{message.in_reply_to} {message.references}"
            if thread_id not in thread:
                continue
            if require == "any_reply":
                return ProofResult(
                    state=ProofState.PASS,
                    evidence_raw=message.text,
                    detail=f"reply from {message.from_addr}",
                )
            return self._classify(message)
        return ProofResult(
            state=ProofState.PENDING,
            evidence_raw="",
            detail="no reply yet",
        )

    def _classify(self, message: ImapMessage) -> ProofResult:
        cleaned = strip_quoted(message.text)
        try:
            label = complete_json(
                self._llm,
                "Classify this reply as positive, negative, or unclear.",
                cleaned,
                {"label": "positive|negative|unclear"},
            )["label"]
        except (LLMError, KeyError):
            return ProofResult(
                state=ProofState.PENDING,
                evidence_raw=cleaned,
                detail="classification failed",
            )
        if label == "positive":
            return ProofResult(
                state=ProofState.PASS,
                evidence_raw=cleaned,
                detail="positive reply",
            )
        if label == "negative":
            return ProofResult(
                state=ProofState.FAIL,
                evidence_raw=cleaned,
                detail="negative reply",
            )
        return ProofResult(
            state=ProofState.PENDING,
            evidence_raw=cleaned,
            detail="unclear reply, escalation suggested",
        )
