import re
import tomllib
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# Phase 2 (adapters) extends this set when new verifier adapters land.
KNOWN_ADAPTERS = frozenset({"payment_email"})

_NAME_RE = re.compile(r"^[a-z0-9-]{3,48}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_DIGIT_RE = re.compile(r"[0-9]")


class GoalParseError(ValueError):
    pass


@dataclass(frozen=True)
class GoalError:
    code: str
    detail: str


@dataclass(frozen=True)
class Target:
    amount: Decimal | None
    currency: str | None
    deadline: datetime | None


@dataclass(frozen=True)
class BudgetLimit:
    money: Decimal | None
    tokens: int | None
    rounds: int | None


@dataclass(frozen=True)
class Settle:
    adapter: str
    rules: tuple


@dataclass(frozen=True)
class LadderRung:
    rung: str
    expected_wait_hours: float
    max_pending_hours: float | None = None


@dataclass(frozen=True)
class StrategyDefaults:
    max_rounds: int = 3
    max_loss: Decimal = Decimal("10.00")
    max_pending_hours: float = 168


@dataclass(frozen=True)
class Assumption:
    text: str
    source: str


@dataclass(frozen=True)
class Goal:
    name: str
    created: datetime | None
    statement: str
    target: Target | None
    budget: BudgetLimit | None
    settle: Settle | None
    ladder: tuple = ()
    strategy_defaults: StrategyDefaults = field(default_factory=StrategyDefaults)
    assumptions: tuple = ()
    body: str = ""
    dir_name: str = ""


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def parse(path: str | Path) -> Goal:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "+++":
        raise GoalParseError(f"{path}: front matter must open with +++")
    try:
        close = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "+++"
        )
    except StopIteration:
        raise GoalParseError(f"{path}: front matter without closing +++") from None
    try:
        front = tomllib.loads("\n".join(lines[1:close]))
    except tomllib.TOMLDecodeError as error:
        raise GoalParseError(f"{path}: bad front matter: {error}") from None
    body = "\n".join(lines[close + 1 :])
    target = front.get("target")
    budget = front.get("budget")
    settle = front.get("settle")
    return Goal(
        name=front.get("name", ""),
        created=front.get("created"),
        statement=front.get("statement", ""),
        target=Target(
            amount=_decimal(target.get("amount")) if target else None,
            currency=target.get("currency") if target else None,
            deadline=target.get("deadline") if target else None,
        )
        if target is not None
        else None,
        budget=BudgetLimit(
            money=_decimal(budget.get("money")) if budget else None,
            tokens=budget.get("tokens") if budget else None,
            rounds=budget.get("rounds") if budget else None,
        )
        if budget is not None
        else None,
        settle=Settle(
            adapter=settle.get("adapter", ""),
            rules=tuple(settle.get("rules", ())),
        )
        if settle is not None
        else None,
        ladder=tuple(
            LadderRung(
                rung=item.get("rung", ""),
                expected_wait_hours=item.get("expected_wait_hours", 0),
                max_pending_hours=item.get("max_pending_hours"),
            )
            for item in front.get("ladder", [])
        ),
        strategy_defaults=_defaults(front.get("strategy_defaults", {})),
        assumptions=tuple(
            Assumption(text=item.get("text", ""), source=item.get("source", ""))
            for item in front.get("assumptions", [])
        ),
        body=body,
        dir_name=path.parent.name,
    )


def _defaults(data: dict) -> StrategyDefaults:
    return StrategyDefaults(
        max_rounds=data.get("max_rounds", 3),
        max_loss=_decimal(data.get("max_loss", "10.00")),
        max_pending_hours=data.get("max_pending_hours", 168),
    )


def validate(goal: Goal, config, now: datetime) -> list:
    errors = []
    rule_names = {rule.name for rule in config.payment_rules}
    if (
        goal.settle is None
        or goal.settle.adapter not in KNOWN_ADAPTERS
        or any(rule not in rule_names for rule in goal.settle.rules)
    ):
        errors.append(
            GoalError(
                "missing_verifier",
                "settle needs a known adapter and configured rule names",
            )
        )
    budget = goal.budget
    if (
        budget is None
        or budget.money is None
        or budget.money <= 0
        or budget.tokens is None
        or budget.tokens <= 0
        or budget.rounds is None
        or budget.rounds <= 0
    ):
        errors.append(
            GoalError("missing_budget", "budget needs money, tokens, rounds above zero")
        )
    target = goal.target
    if target is None or target.deadline is None or target.deadline <= now:
        errors.append(
            GoalError("missing_deadline", "target deadline must be in the future")
        )
    if (
        target is None
        or target.amount is None
        or target.amount <= 0
        or not target.currency
        or not _CURRENCY_RE.match(target.currency)
    ):
        errors.append(
            GoalError(
                "missing_target", "target needs a positive amount and 3-letter currency"
            )
        )
    names = [rung.rung for rung in goal.ladder]
    if (
        not 3 <= len(names) <= 5
        or len(set(names)) != len(names)
        or (names[-1:] != ["settled"])
    ):
        errors.append(
            GoalError("bad_ladder", "ladder needs 3-5 unique rungs ending in settled")
        )
    if not _NAME_RE.match(goal.name) or goal.name != goal.dir_name:
        errors.append(
            GoalError("bad_name", "name must match [a-z0-9-]{3,48} and directory name")
        )
    if not goal.statement or not _DIGIT_RE.search(goal.statement):
        errors.append(
            GoalError("unmeasurable_statement", "statement must contain a number")
        )
    return errors


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(goal: Goal) -> str:
    lines = [f"name = {_quote(goal.name)}"]
    if goal.created is not None:
        lines.append(f"created = {goal.created.isoformat()}")
    lines.append(f"statement = {_quote(goal.statement)}")
    lines.append("")
    if goal.target is not None:
        lines.append("[target]")
        if goal.target.amount is not None:
            lines.append(f"amount = {_quote(str(goal.target.amount))}")
        if goal.target.currency is not None:
            lines.append(f"currency = {_quote(goal.target.currency)}")
        if goal.target.deadline is not None:
            lines.append(f"deadline = {goal.target.deadline.isoformat()}")
        lines.append("")
    if goal.budget is not None:
        lines.append("[budget]")
        if goal.budget.money is not None:
            lines.append(f"money = {_quote(str(goal.budget.money))}")
        if goal.budget.tokens is not None:
            lines.append(f"tokens = {goal.budget.tokens}")
        if goal.budget.rounds is not None:
            lines.append(f"rounds = {goal.budget.rounds}")
        lines.append("")
    if goal.settle is not None:
        lines.append("[settle]")
        lines.append(f"adapter = {_quote(goal.settle.adapter)}")
        rules = ", ".join(_quote(rule) for rule in goal.settle.rules)
        lines.append(f"rules = [{rules}]")
        lines.append("")
    for rung in goal.ladder:
        lines.append("[[ladder]]")
        lines.append(f"rung = {_quote(rung.rung)}")
        lines.append(f"expected_wait_hours = {rung.expected_wait_hours}")
        if rung.max_pending_hours is not None:
            lines.append(f"max_pending_hours = {rung.max_pending_hours}")
    if goal.ladder:
        lines.append("")
    lines.append("[strategy_defaults]")
    lines.append(f"max_rounds = {goal.strategy_defaults.max_rounds}")
    lines.append(f"max_loss = {_quote(str(goal.strategy_defaults.max_loss))}")
    lines.append(f"max_pending_hours = {goal.strategy_defaults.max_pending_hours}")
    lines.append("")
    for assumption in goal.assumptions:
        lines.append("[[assumptions]]")
        lines.append(f"text = {_quote(assumption.text)}")
        lines.append(f"source = {_quote(assumption.source)}")
    if goal.assumptions:
        lines.append("")
    front = "\n".join(lines).rstrip("\n")
    return f"+++\n{front}\n+++\n{goal.body}"


def append_assumption(path: str | Path, text: str, source: str) -> None:
    path = Path(path)
    goal = parse(path)
    updated = replace(goal, assumptions=goal.assumptions + (Assumption(text, source),))
    path.write_text(render(updated), encoding="utf-8")
