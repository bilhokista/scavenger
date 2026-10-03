from datetime import UTC, datetime
from decimal import Decimal

from scavenger.adapters import ProofState
from scavenger.adapters.inbox import ImapMessage
from scavenger.adapters.payment_email import PaymentEmailAdapter
from scavenger.config import PaymentRule

SINCE = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)

BANK_RULE = PaymentRule(
    name="example-bank",
    from_regex=r"noreply@bank\.example",
    subject_regex=r"(?i)incoming transfer",
    amount_regex=r"(?P<amount>[0-9.,]+)",
    decimal_separator=".",
    currency="USD",
    currency_regex="",
    reference_regex=r"(?i)ref(?:erence)?[: ]+(?P<ref>\S+)",
)

LOCATOR = {
    "rules": ["example-bank"],
    "expected_amount": "100.00",
    "currency": "USD",
    "reference": "INV-12",
    "tolerance": "0.01",
}


def email(**overrides):
    base = {
        "message_id": "<pay-1@bank.example>",
        "from_addr": "noreply@bank.example",
        "date": datetime(2026, 10, 3, 6, 0, tzinfo=UTC),
        "in_reply_to": "",
        "references": "",
        "subject": "Incoming transfer received",
        "text": "Amount 100.00 USD Reference: INV-12",
    }
    base.update(overrides)
    return ImapMessage(**base)


class FakeImap:
    def __init__(self, messages=None, error=None):
        self._messages = messages or []
        self._error = error

    def list_since(self, since):
        if self._error is not None:
            raise self._error
        return [message for message in self._messages if message.date >= since]


def adapter(messages, rules=(BANK_RULE,), error=None):
    return PaymentEmailAdapter(FakeImap(messages, error), rules)


def test_matching_notice_passes_with_settlement():
    result = adapter([email()]).check("settled", LOCATOR, SINCE)
    assert result.state == ProofState.PASS
    assert result.settlement is not None
    assert result.settlement.amount == Decimal("100.00")
    assert result.settlement.currency == "USD"
    assert result.settlement.message_id == "<pay-1@bank.example>"


def test_amount_outside_tolerance_pending():
    result = adapter([email(text="Amount 50.00 USD Reference: INV-12")]).check(
        "settled", LOCATOR, SINCE
    )
    assert result.state == ProofState.PENDING
    assert result.settlement is None


def test_wrong_reference_pending():
    result = adapter([email(text="Amount 100.00 USD Reference: INV-99")]).check(
        "settled", LOCATOR, SINCE
    )
    assert result.state == ProofState.PENDING


def test_comma_decimal_rule():
    rule = PaymentRule(
        name="euro-bank",
        from_regex=r"noreply@euro\.example",
        subject_regex=r"(?i)incoming transfer",
        amount_regex=r"(?P<amount>[0-9.,]+)",
        decimal_separator=",",
        currency="EUR",
        currency_regex="",
        reference_regex=r"(?i)ref(?:erence)?[: ]+(?P<ref>\S+)",
    )
    mail = email(
        from_addr="noreply@euro.example",
        text="Amount 1.234,56 EUR Reference: INV-12",
    )
    locator = dict(
        LOCATOR, rules=["euro-bank"], expected_amount="1234.56", currency="EUR"
    )
    result = adapter([mail], rules=(rule,)).check("settled", locator, SINCE)
    assert result.state == ProofState.PASS
    assert result.settlement.amount == Decimal("1234.56")


def test_sender_not_in_rules_ignored():
    result = adapter([email(from_addr="spam@evil.example")]).check(
        "settled", LOCATOR, SINCE
    )
    assert result.state == ProofState.PENDING


def test_currency_from_regex():
    rule = PaymentRule(
        name="multi-bank",
        from_regex=r"noreply@multi\.example",
        subject_regex=r"(?i)incoming transfer",
        amount_regex=r"(?P<amount>[0-9.,]+) (?P<currency>[A-Z]{3})",
        decimal_separator=".",
        currency="",
        currency_regex=r"(?P<currency>[A-Z]{3})",
        reference_regex=r"(?i)ref(?:erence)?[: ]+(?P<ref>\S+)",
    )
    mail = email(
        from_addr="noreply@multi.example",
        text="Amount 100.00 USD Reference: INV-12",
    )
    locator = dict(LOCATOR, rules=["multi-bank"])
    result = adapter([mail], rules=(rule,)).check("settled", locator, SINCE)
    assert result.state == ProofState.PASS
    assert result.settlement.currency == "USD"


def test_unparseable_amount_logged_not_passed(caplog):
    import logging

    result = None
    with caplog.at_level(logging.WARNING, logger="scavenger.adapters.payment_email"):
        result = adapter([email(text="Amount unknown, see attached")]).check(
            "settled", LOCATOR, SINCE
        )
    assert result.state == ProofState.PENDING
    assert caplog.records
