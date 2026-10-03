from decimal import Decimal

import pytest
from scavenger.clock import now
from scavenger.money import AmountError, convert, parse_amount


def test_now_is_utc_aware():
    ts = now()
    assert ts.tzinfo is not None
    assert ts.utcoffset().total_seconds() == 0


def test_parse_dot_decimal_with_comma_thousands():
    assert parse_amount("$1,234.56", ".") == Decimal("1234.56")


def test_parse_comma_decimal_with_dot_thousands():
    assert parse_amount("Rp 1.234,56", ",") == Decimal("1234.56")


def test_parse_rejects_two_decimal_marks():
    with pytest.raises(AmountError):
        parse_amount("12.34.56", ".")


def test_parse_rejects_letters():
    with pytest.raises(AmountError):
        parse_amount("12a34.56", ".")


def test_convert_same_currency():
    amount = Decimal("0.1")
    result = convert(amount, "USD", "USD", {})
    assert result == Decimal("0.1")
    assert isinstance(result, Decimal)


def test_convert_missing_pair_returns_none():
    assert convert(Decimal(10), "USD", "IDR", {}) is None


def test_convert_uses_decimal_not_float():
    result = convert(Decimal(10), "USD", "IDR", {("USD", "IDR"): Decimal(16000)})
    assert result == Decimal(160000)
    assert isinstance(result, Decimal)
