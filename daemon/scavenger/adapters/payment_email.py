import logging
import re
from datetime import datetime
from decimal import Decimal

from scavenger.adapters import Adapter, ProofResult, ProofState, Settlement
from scavenger.money import AmountError, parse_amount

log = logging.getLogger(__name__)


class PaymentEmailAdapter(Adapter):
    name = "payment_email"

    def __init__(self, imap, rules) -> None:
        self._imap = imap
        self._rules = {rule.name: rule for rule in rules}

    def check(self, rung: str, locator: dict, since: datetime) -> ProofResult:
        expected = Decimal(str(locator["expected_amount"]))
        currency = locator["currency"]
        reference = locator.get("reference")
        tolerance = Decimal(str(locator.get("tolerance", "0")))
        try:
            messages = self._imap.list_since(since)
        except (ConnectionError, OSError):
            return ProofResult(
                state=ProofState.UNVERIFIED,
                evidence_raw="",
                detail="imap error",
            )
        for message in messages:
            for rule_name in locator.get("rules", ()):
                rule = self._rules.get(rule_name)
                if rule is None:
                    continue
                if not re.search(rule.from_regex, message.from_addr):
                    continue
                if not re.search(rule.subject_regex, message.subject):
                    continue
                found = self._extract(message, rule)
                if found is None:
                    continue
                amount, extracted_currency, extracted_ref = found
                if abs(amount - expected) > tolerance:
                    continue
                if extracted_currency != currency:
                    continue
                if reference is not None and extracted_ref != reference:
                    continue
                return ProofResult(
                    state=ProofState.PASS,
                    evidence_raw=f"{message.subject}\n{message.text}",
                    detail=f"payment {amount} {extracted_currency}",
                    settlement=Settlement(
                        amount=amount,
                        currency=extracted_currency,
                        message_id=message.message_id,
                    ),
                )
        return ProofResult(
            state=ProofState.PENDING,
            evidence_raw="",
            detail="no matching notice",
        )

    def _extract(self, message, rule):
        combined = f"{message.subject}\n{message.text}"
        amount_match = re.search(rule.amount_regex, combined)
        if amount_match is None:
            log.warning("no amount in message %s", message.message_id)
            return None
        try:
            amount = parse_amount(amount_match.group("amount"), rule.decimal_separator)
        except (AmountError, IndexError):
            log.warning("unparseable amount in message %s", message.message_id)
            return None
        if rule.currency:
            extracted_currency = rule.currency
        elif rule.currency_regex:
            currency_match = re.search(rule.currency_regex, combined)
            if currency_match is None:
                log.warning("no currency in message %s", message.message_id)
                return None
            try:
                extracted_currency = currency_match.group("currency")
            except IndexError:
                log.warning(
                    "currency regex without group in message %s",
                    message.message_id,
                )
                return None
        else:
            extracted_currency = ""
        extracted_ref = None
        if rule.reference_regex:
            ref_match = re.search(rule.reference_regex, combined)
            if ref_match is not None:
                try:
                    extracted_ref = ref_match.group("ref")
                except IndexError:
                    extracted_ref = None
        return amount, extracted_currency, extracted_ref
