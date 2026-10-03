import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from scavenger import brake
from scavenger.goal import parse as parse_goal


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    patch_path: str | None
    description_path: str | None
    detail: str


class Executor:
    def __init__(self, store, config, channels: dict, outbox, clock) -> None:
        self._store = store
        self._config = config
        self._channels = channels
        self._outbox = outbox
        self._clock = clock

    def run_round(self, strategy_id: int) -> str:
        store = self._store
        strategy = store.get_strategy(strategy_id)
        goal = parse_goal(self._goal_path(store.get_mission(strategy.mission)))
        verdict = brake.strategy_verdict(strategy, goal)
        if verdict is not None:
            self._close(strategy, verdict.cause)
            return f"dead:{verdict.cause}"
        for item in store.list_outbox("awaiting") + store.list_outbox("approved"):
            if item.strategy_id == strategy_id:
                return "skipped:open-outbox"
        channel = self._channels[strategy.channel]
        checks = store.last_proof_checks(strategy_id, 1)
        action = channel.next_action(strategy, checks[0] if checks else None)
        round_id = store.start_round(
            strategy_id,
            started_at=self._clock.now().isoformat(),
            action_kind=action.kind,
            action_fingerprint=action.fingerprint,
        )
        if action.tokens_in + action.tokens_out > 0:
            store.add_spend(
                mission=strategy.mission,
                strategy_id=strategy_id,
                at=self._clock.now().isoformat(),
                tokens_in=action.tokens_in,
                tokens_out=action.tokens_out,
                money=Decimal(0),
                source="llm",
            )
        if action.kind == "draft":
            self._outbox.create_draft(action.draft, strategy.mission, strategy_id)
            return "drafted"
        if action.kind == "local":
            return self._run_local(strategy, round_id, action)
        store.end_round(
            round_id,
            ended_at=self._clock.now().isoformat(),
            result_state="pending",
        )
        return "waited"

    def _run_local(self, strategy, round_id: int, action) -> str:
        return self.run_agent(strategy, action.description)

    def run_agent(self, strategy, instructions: str):
        raise NotImplementedError("task 5.3")

    def _goal_path(self, mission) -> Path:
        path = Path(mission.goal_path)
        if path.is_absolute():
            return path
        return self._config.paths.missions.parent / path

    def _close(self, strategy, cause: str) -> None:
        totals = self._store.strategy_spend(strategy.id)
        try:
            rungs = json.loads(strategy.rungs_json)
            rung = rungs[strategy.rung_index].get("rung", "")
        except (ValueError, IndexError, AttributeError):
            rung = ""
        self._store.close_strategy(
            strategy.id,
            status="dead",
            death_cause=cause,
            rung_reached=rung,
            failed_rounds=strategy.failed_rounds,
            money_spent=totals.money,
            tokens=totals.tokens_in + totals.tokens_out,
            hours_to_rung_json="{}",
            closed_at=self._clock.now().isoformat(),
        )
