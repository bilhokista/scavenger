import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scavenger import clock
from scavenger.channels import Action, Liveness
from scavenger.channels.rss import parse_feed
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
        rounds = []
        for entry in parse_feed(text):
            amount = self._parse_amount(f"{entry.title}\n{entry.body}")
            if amount is None or entry.published is None:
                continue
            rounds.append(
                {
                    "name": entry.title,
                    "url": entry.link,
                    "amount": amount,
                    "currency": "USD",
                    "deadline": entry.published + timedelta(days=self._window),
                    "requirements": entry.body,
                }
            )
        return rounds

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
