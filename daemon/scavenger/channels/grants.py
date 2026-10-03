import email.utils
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scavenger import clock
from scavenger.channels import Action, Liveness
from scavenger.outbox import DraftSpec
from scavenger.switcher import Candidate

_AMOUNT_RE = re.compile(r"\$([\d,]+(?:\.\d+)?)")


class GrantsChannel:
    name = "grants"

    def __init__(
        self, feeds, http_client, llm, rounds, default_window_days: int = 90
    ) -> None:
        self._feeds = list(feeds)
        self._http = http_client
        self._llm = llm
        self._rounds = list(rounds)
        self._window = default_window_days
        self._goal = None

    def liveness(self) -> Liveness:
        return Liveness(
            alive=True,
            evidence_urls=tuple(self._feeds),
            detail="configured feeds and rounds",
            checked_at=clock.now(),
        )

    def discover(self, goal) -> list:
        self._goal = goal
        now = clock.now()
        rounds = list(self._rounds)
        for url in self._feeds:
            try:
                response = self._http.get(url)
            except Exception:  # noqa: BLE001, S112 - dead feed means no rounds
                continue
            if getattr(response, "status_code", 200) != 200:
                continue
            rounds.extend(self._parse_feed(response.text, url))
        found = []
        for entry in rounds:
            deadline = entry["deadline"]
            if isinstance(deadline, str):
                deadline = self._parse_iso(deadline)
            if deadline is None or deadline <= now:
                continue
            if entry["amount"] is None:
                continue
            found.append(
                Candidate(
                    channel="grants",
                    path=entry["name"],
                    path_key=f"grants:{entry['url']}",
                    outward_key=f"form:{entry['url']}",
                    payout_amount=Decimal(str(entry["amount"])),
                    payout_currency=entry.get("currency", "USD"),
                    guarantor="grant",
                    guarantor_evidence=entry["url"],
                    capital_needed=Decimal(0),
                    hours_first_proof=168.0,
                    hours_settlement=336.0,
                    probability=0.3,
                    probability_note="configured round, no ledger history",
                    source_urls=(entry["url"],),
                    rungs=(
                        {"rung": "submitted", "adapter": "attested", "locator": {}},
                        {"rung": "accepted", "adapter": "inbox", "locator": {}},
                        {"rung": "settled", "adapter": "payment_email", "locator": {}},
                    ),
                )
            )
        return found

    def _parse_feed(self, text: str, url: str) -> list:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return []
        entries = []
        for item in root.iter("item"):
            title = self._text(item, "title")
            link = self._text(item, "link")
            body = self._text(item, "description")
            published = self._parse_date(self._text(item, "pubDate"))
            entries.append((title, link, body, published, url))
        for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
            title = self._atom_text(entry, "title")
            link = ""
            for node in entry.iter("{http://www.w3.org/2005/Atom}link"):
                link = node.get("href", "")
                break
            body = self._atom_text(entry, "summary")
            published = self._parse_iso(self._atom_text(entry, "updated"))
            entries.append((title, link, body, published, url))
        rounds = []
        for title, link, body, published, _ in entries:
            amount = self._parse_amount(f"{title}\n{body}")
            if amount is None or published is None:
                continue
            rounds.append(
                {
                    "name": title,
                    "url": link,
                    "amount": amount,
                    "currency": "USD",
                    "deadline": published + timedelta(days=self._window),
                    "requirements": body,
                }
            )
        return rounds

    @staticmethod
    def _text(item, tag: str) -> str:
        node = item.find(tag)
        return node.text.strip() if node is not None and node.text else ""

    @staticmethod
    def _atom_text(entry, tag: str) -> str:
        node = entry.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
        return node.text.strip() if node is not None and node.text else ""

    @staticmethod
    def _parse_date(value: str):
        if not value:
            return None
        try:
            moment = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment

    @staticmethod
    def _parse_iso(value: str):
        if not value:
            return None
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment

    @staticmethod
    def _parse_amount(text: str):
        match = _AMOUNT_RE.search(text)
        if match is None:
            return None
        return Decimal(match.group(1).replace(",", ""))

    def next_action(self, strategy, last_check) -> Action:
        rung = self._rung_name(strategy)
        if rung == "submitted" and self._goal is not None:
            requirements = self._requirements_for(strategy)
            result = self._llm.complete(
                "Write a grant application. Plain text, no flattery.",
                f"GOAL: {self._goal.statement}\n"
                f"ROUND: {requirements}\n"
                "Write the full application text.",
            )
            return Action(
                kind="draft",
                draft=DraftSpec(
                    kind="manual",
                    target=self._round_url(strategy),
                    body=result.text,
                ),
                description="draft grant application",
                fingerprint=f"grants-apply:{strategy.path_key}",
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
            )
        return Action(
            kind="wait",
            draft=None,
            description="wait for round decision",
            fingerprint=f"grants-wait:{strategy.path_key}",
        )

    def _rung_name(self, strategy) -> str:
        import json

        try:
            rungs = json.loads(strategy.rungs_json)
            return rungs[strategy.rung_index].get("rung", "")
        except (ValueError, IndexError, AttributeError):
            return ""

    def _round_url(self, strategy) -> str:
        if strategy.outward_key.startswith("form:"):
            return strategy.outward_key[len("form:") :]
        return strategy.outward_key

    def _requirements_for(self, strategy) -> str:
        for entry in self._rounds:
            if entry["url"] == self._round_url(strategy):
                return entry.get("requirements", "")
        return ""
