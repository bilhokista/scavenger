from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Death:
    cause: str


@dataclass(frozen=True)
class MissionStop:
    stop: str


def strategy_verdict(strategy, goal) -> Death | None:
    if strategy.failed_rounds >= goal.strategy_defaults.max_rounds:
        return Death("round_limit")
    if strategy.loss >= goal.strategy_defaults.max_loss:
        return Death("loss_limit")
    return None


def mission_verdict(mission, goal, store, now: datetime) -> MissionStop | None:
    settled = store.settled_in_target(mission.name)
    if settled >= goal.target.amount:
        return MissionStop("done")
    if now > goal.target.deadline:
        return MissionStop("stopped_deadline")
    spent = store.spend_since(mission.name, "0001-01-01T00:00:00+00:00")
    failed = sum(
        strategy.failed_rounds for strategy in store.list_strategies(mission.name)
    )
    if (
        spent.money >= goal.budget.money
        or spent.tokens_in + spent.tokens_out >= goal.budget.tokens
        or failed >= goal.budget.rounds
    ):
        return MissionStop("stopped_budget")
    live = {"pending", "active", "queued", "awaiting_approval"}
    if mission.empty_refills >= 2 and not any(
        strategy.status in live for strategy in store.list_strategies(mission.name)
    ):
        return MissionStop("impossible")
    return None
