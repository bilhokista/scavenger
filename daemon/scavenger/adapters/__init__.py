from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol


class ProofState(StrEnum):
    PASS = "pass"
    PENDING = "pending"
    FAIL = "fail"
    UNVERIFIED = "unverified"


@dataclass(frozen=True)
class Settlement:
    amount: Decimal
    currency: str
    message_id: str


@dataclass(frozen=True)
class ProofResult:
    state: ProofState
    evidence_raw: str
    detail: str
    settlement: Settlement | None = None


class Adapter(Protocol):
    name: str

    def check(
        self, rung: str, locator: Mapping[str, Any], since: datetime
    ) -> ProofResult: ...


class UnknownAdapter(Exception):
    pass


_REGISTRY: dict = {}


def register(adapter: Adapter) -> None:
    _REGISTRY[adapter.name] = adapter


def get(name: str) -> Adapter:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownAdapter(f"unknown adapter: {name}") from None
