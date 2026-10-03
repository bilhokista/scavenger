import hashlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from scavenger.goal import (
    Assumption,
    BudgetLimit,
    Goal,
    LadderRung,
    Settle,
    StrategyDefaults,
    Target,
    validate,
)
from scavenger.goal import (
    render as render_goal,
)
from scavenger.llm import complete_json

NEVER_ASSUMED = ("missing_verifier", "missing_budget", "missing_deadline")

QUESTIONS = {
    "missing_verifier": (
        "How will we prove the money arrived"
        " (which sender and rule, e.g. a bank email notice)?"
    ),
    "missing_budget": (
        "What is the max spend for this mission (money, tokens, rounds)?"
    ),
    "missing_deadline": "When must the money land by?",
    "missing_target": "How much money, in what currency?",
    "bad_ladder": "Name 3-5 proof steps ending in 'settled'.",
    "bad_name": "Give this mission a short lowercase name.",
    "unmeasurable_statement": "What exactly is bleeding, with a number?",
}

_GOAL_SCHEMA = {"type": "object"}


class MissingRequiredField(Exception):
    def __init__(self, field: str) -> None:
        super().__init__(f"missing required field: {field}")
        self.field = field


def _goal_from_dict(data: dict) -> Goal:
    target = data.get("target") or {}
    budget = data.get("budget") or {}
    settle = data.get("settle")
    deadline = target.get("deadline")
    return Goal(
        name=data.get("name", ""),
        created=_parse_time(data.get("created")),
        statement=data.get("statement", ""),
        target=Target(
            amount=_decimal(target.get("amount")),
            currency=target.get("currency"),
            deadline=_parse_time(deadline),
        ),
        budget=BudgetLimit(
            money=_decimal(budget.get("money")),
            tokens=budget.get("tokens"),
            rounds=budget.get("rounds"),
        ),
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
            for item in data.get("ladder", [])
        ),
        strategy_defaults=_defaults(data.get("strategy_defaults", {})),
        assumptions=tuple(
            Assumption(text=item.get("text", ""), source=item.get("source", ""))
            for item in data.get("assumptions", [])
        ),
        body=data.get("body", ""),
        dir_name=data.get("name", ""),
    )


def _decimal(value):
    if value is None:
        return None
    return Decimal(str(value))


def _parse_time(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _defaults(data: dict) -> StrategyDefaults:
    return StrategyDefaults(
        max_rounds=data.get("max_rounds", 3),
        max_loss=_decimal(data.get("max_loss", "10.00")),
        max_pending_hours=data.get("max_pending_hours", 168),
    )


def _prioritize(errors: list) -> list:
    never = [error for error in errors if error.code in NEVER_ASSUMED]
    rest = [error for error in errors if error.code not in NEVER_ASSUMED]
    return never + rest


def compile_brief(brief: str, ask, config, *, llm, store, clock) -> Goal:
    conversation = brief
    draft = complete_json(
        llm,
        "Turn the brief into a goal contract. Reply JSON only.",
        conversation,
        _GOAL_SCHEMA,
    )
    asked = 0
    asked_codes = set()
    silence_notes = []
    while True:
        goal = _goal_from_dict(draft)
        errors = validate(goal, config, clock.now())
        remaining = [
            error for error in _prioritize(errors) if error.code not in asked_codes
        ]
        if not remaining or asked >= 3:
            break
        error = remaining[0]
        question = QUESTIONS[error.code]
        answer = ask(question)
        asked += 1
        asked_codes.add(error.code)
        if answer is None and error.code not in NEVER_ASSUMED:
            silence_notes.append(f"Unconfirmed: {question}")
        conversation += f"\nQ: {question}\nA: {answer or '(silence)'}"
        draft = complete_json(
            llm,
            "Patch the goal contract with the answer. Reply JSON only.",
            conversation,
            _GOAL_SCHEMA,
        )
    goal = _goal_from_dict(draft)
    for note in silence_notes:
        goal = _with_assumption(goal, note)
    errors = validate(goal, config, clock.now())
    if errors:
        raise MissingRequiredField(_prioritize(errors)[0].code)
    missions_dir = config.paths.missions
    mission_dir = missions_dir / goal.name
    mission_dir.mkdir(parents=True, exist_ok=True)
    goal_file = mission_dir / "GOAL.md"
    goal_file.write_text(render_goal(goal), encoding="utf-8")
    digest = hashlib.sha256(goal_file.read_bytes()).hexdigest()
    store.create_mission(
        name=goal.name,
        goal_path=str(Path("missions") / goal.name / "GOAL.md"),
        goal_sha256=digest,
        status="active",
        target_amount=goal.target.amount,
        target_currency=goal.target.currency,
        deadline=goal.target.deadline.isoformat(),
        created_at=clock.now().isoformat(),
    )
    return goal


def _with_assumption(goal: Goal, note: str) -> Goal:
    from dataclasses import replace

    return replace(
        goal,
        assumptions=goal.assumptions + (Assumption(text=note, source="silence"),),
    )
