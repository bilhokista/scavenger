import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


class AmountError(ValueError):
    pass


_CURRENCY_SYMBOLS = ("Rp", "rp", "$", "€", "£", "¥", "₹")

_NUMERIC_RE = re.compile(r"^-?[0-9.,]+$")


def parse_amount(text: str, decimal_separator: str) -> Decimal:
    if decimal_separator not in (".", ","):
        raise AmountError(f"unknown decimal separator: {decimal_separator!r}")
    cleaned = text.strip().replace(" ", "")
    for symbol in _CURRENCY_SYMBOLS:
        if cleaned.startswith(symbol):
            cleaned = cleaned[len(symbol) :]
            break
    if not cleaned or not _NUMERIC_RE.match(cleaned):
        raise AmountError(f"not an amount: {text!r}")
    thousands = "," if decimal_separator == "." else "."
    digits = cleaned.replace(thousands, "")
    if digits.count(decimal_separator) > 1:
        raise AmountError(f"two decimal marks: {text!r}")
    if decimal_separator != ".":
        digits = digits.replace(decimal_separator, ".")
    try:
        return Decimal(digits)
    except InvalidOperation:
        raise AmountError(f"not an amount: {text!r}") from None


def convert(
    amount: Decimal,
    from_ccy: str,
    to_ccy: str,
    fx: Mapping[tuple, Decimal],
) -> Decimal | None:
    if from_ccy.upper() == to_ccy.upper():
        return amount
    rate = fx.get((from_ccy.upper(), to_ccy.upper()))
    if rate is None:
        return None
    return amount * rate
