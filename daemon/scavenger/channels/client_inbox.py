import re
from datetime import timedelta
from decimal import Decimal

from scavenger import clock
from scavenger.channels import Action, Liveness
from scavenger.channels.rss import parse_feed
from scavenger.outbox import DraftSpec
from scavenger.switcher import Candidate

_AMOUNT_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)")


class ClientInboxChannel:
    # Two cited sources only: inbound mail matching configured filters,
    # and public job postings. No cold outreach: a candidate without a
    # cited message id or posting URL is never created.
    name = "client-inbox"

    def __init__(
        self,
        imap_client,
        feeds,
        http_client,
        filters,
        llm,
        lookback_days: int = 30,
        posting_window_days: int = 30,
    ) -> None:
        self._imap = imap_client
        self._feeds = list(feeds)
        self._http = http_client
        self._filters = list(filters)
        self._llm = llm
        self._lookback = lookback_days
        self._posting_window = posting_window_days
        self._goal = None
        self._requests = {}

    def liveness(self) -> Liveness:
        return Liveness(
            alive=True,
            evidence_urls=tuple(self._feeds),
            detail="mailbox plus configured feeds",
            checked_at=clock.now(),
        )

    def discover(self, goal) -> list:
        self._goal = goal
        now = clock.now()
        found = []
        try:
            messages = self._imap.list_since(now - timedelta(days=self._lookback))
        except (ConnectionError, OSError):
            messages = []
        for message in messages:
            if not self._matches(message):
                continue
            amount = self._parse_amount(f"{message['subject']}\n{message['text']}")
            if amount is None:
                amount = goal.target.amount
            key = f"inbox:{message['message_id']}"
            self._requests[key] = message["text"]
            found.append(
                self._candidate(
                    path=message["subject"][:80],
                    path_key=key,
                    outward_key=f"email:{message['from_addr']}",
                    amount=amount,
                    url="",
                )
            )
        for url in self._feeds:
            try:
                response = self._http.get(url)
            except Exception:  # noqa: BLE001, S112 - dead feed, no postings
                continue
            if getattr(response, "status_code", 200) != 200:
                continue
            for entry in parse_feed(response.text):
                if not entry.link:
                    continue
                if entry.published is None:
                    continue
                if entry.published < now - timedelta(days=self._posting_window):
                    continue
                amount = self._parse_amount(f"{entry.title}\n{entry.body}")
                if amount is None:
                    amount = goal.target.amount
                key = f"jobs:{entry.link}"
                self._requests[key] = entry.body
                found.append(
                    self._candidate(
                        path=entry.title[:80],
                        path_key=key,
                        outward_key=f"form:{entry.link}",
                        amount=amount,
                        url=entry.link,
                    )
                )
        return found

    def _matches(self, message) -> bool:
        for filtr in self._filters:
            subject_ok = not filtr.subject_regex or re.search(
                filtr.subject_regex, message["subject"]
            )
            body_ok = not filtr.body_regex or re.search(
                filtr.body_regex, message["text"]
            )
            if subject_ok and body_ok:
                return True
        return False

    @staticmethod
    def _parse_amount(text: str):
        match = _AMOUNT_RE.search(text)
        if match is None:
            return None
        return Decimal(match.group(1).replace(",", ""))

    def _candidate(self, path, path_key, outward_key, amount, url):
        return Candidate(
            channel="client-inbox",
            path=path,
            path_key=path_key,
            outward_key=outward_key,
            payout_amount=Decimal(str(amount)),
            payout_currency="USD",
            guarantor="none",
            guarantor_evidence=None,
            capital_needed=Decimal(0),
            hours_first_proof=24.0,
            hours_settlement=168.0,
            probability=0.2,
            probability_note="inbound request, budget unconfirmed",
            source_urls=(url,) if url else (),
            rungs=(
                {"rung": "submitted", "adapter": "attested", "locator": {}},
                {"rung": "replied", "adapter": "inbox", "locator": {}},
                {"rung": "deposit", "adapter": "payment_email", "locator": {}},
                {"rung": "settled", "adapter": "payment_email", "locator": {}},
            ),
        )

    def next_action(self, strategy, last_check) -> Action:
        import json

        try:
            rung = json.loads(strategy.rungs_json)[strategy.rung_index]["rung"]
        except (ValueError, IndexError, AttributeError):
            rung = ""
        if rung == "submitted" and self._goal is not None:
            request = self._requests.get(strategy.path_key, "")
            result = self._llm.complete(
                "Write a short client proposal. Plain text, no flattery.",
                f"GOAL: {self._goal.statement}\nREQUEST: {request}\n"
                "Write the proposal.",
            )
            if strategy.outward_key.startswith("email:"):
                target = strategy.outward_key[len("email:") :]
                draft = DraftSpec(kind="email", target=target, body=result.text)
            else:
                draft = DraftSpec(
                    kind="manual",
                    target=self._posting_url(strategy),
                    body=result.text,
                )
            return Action(
                kind="draft",
                draft=draft,
                description="draft client proposal",
                fingerprint=f"inbox-proposal:{strategy.path_key}",
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
            )
        return Action(
            kind="wait",
            draft=None,
            description="wait for client reply",
            fingerprint=f"inbox-wait:{strategy.path_key}",
        )

    @staticmethod
    def _posting_url(strategy) -> str:
        if strategy.outward_key.startswith("form:"):
            return strategy.outward_key[len("form:") :]
        return strategy.outward_key
