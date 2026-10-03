import json
from datetime import datetime, timedelta

from scavenger.adapters import ProofResult, ProofState
from scavenger.money import convert


class Verifier:
    def __init__(self, store, config, adapters: dict, clock) -> None:
        self._store = store
        self._config = config
        self._adapters = adapters
        self._clock = clock

    def check_strategy(self, strategy_id: int) -> ProofResult:
        store = self._store
        strategy = store.get_strategy(strategy_id)
        rungs = json.loads(strategy.rungs_json)
        plan = rungs[strategy.rung_index]
        rung = plan["rung"]
        round_row = store.latest_round(strategy_id)
        locator = dict(plan.get("locator", {}))
        if round_row is not None and round_row.locator_json:
            locator.update(json.loads(round_row.locator_json))
        if round_row is not None:
            since = datetime.fromisoformat(round_row.started_at)
        else:
            since = datetime.fromisoformat(strategy.created_at)
        adapter = self._adapters.get(plan["adapter"])
        if adapter is None:
            result = ProofResult(
                state=ProofState.UNVERIFIED,
                evidence_raw="",
                detail=f"unknown adapter: {plan['adapter']}",
            )
        else:
            result = adapter.check(rung, locator, since)
        store.add_proof_check(
            strategy_id,
            round_row.id if round_row else None,
            rung,
            plan["adapter"],
            json.dumps(locator, sort_keys=True),
            result.state.value,
            result.evidence_raw,
            None,
            self._clock.now().isoformat(),
        )
        if result.state == ProofState.PASS:
            self._apply_pass(strategy, rungs, plan, rung, result)
        elif result.state == ProofState.PENDING:
            self._apply_pending(strategy, plan)
        elif result.state == ProofState.FAIL:
            self._apply_fail(strategy, rung, cause="fail")
        else:
            self._apply_unverified(strategy)
        return result

    def _apply_pass(self, strategy, rungs, plan, rung, result) -> None:
        store = self._store
        if result.settlement is not None:
            mission = store.get_mission(strategy.mission)
            amount_in_target = convert(
                result.settlement.amount,
                result.settlement.currency,
                mission.target_currency,
                self._config.fx.rates,
            )
            saved = store.add_settlement(
                mission=strategy.mission,
                strategy_id=strategy.id,
                amount=result.settlement.amount,
                currency=result.settlement.currency,
                amount_in_target=amount_in_target,
                message_id=result.settlement.message_id,
                proof_check_id=self._latest_check_id(strategy.id),
                at=self._clock.now().isoformat(),
            )
            if not saved:
                store.add_event(
                    mission=strategy.mission,
                    level="warn",
                    kind="duplicate_settlement",
                    message=(
                        f"settlement {result.settlement.message_id} already recorded"
                    ),
                )
                if strategy.pending_since is None:
                    store.update_strategy(
                        strategy.id,
                        status="pending",
                        pending_since=self._clock.now().isoformat(),
                        unverified_streak=0,
                    )
                return
        if strategy.rung_index >= len(rungs) - 1:
            totals = store.strategy_spend(strategy.id)
            store.close_strategy(
                strategy.id,
                status="won",
                death_cause=None,
                rung_reached=rung,
                failed_rounds=strategy.failed_rounds,
                money_spent=totals.money,
                tokens=totals.tokens_in + totals.tokens_out,
                hours_to_rung_json="{}",
                closed_at=self._clock.now().isoformat(),
            )
        else:
            store.update_strategy(
                strategy.id,
                rung_index=strategy.rung_index + 1,
                status="active",
                pending_since=None,
                unverified_streak=0,
                next_check_at=None,
            )

    def _apply_pending(self, strategy, plan) -> None:
        store = self._store
        now = self._clock.now()
        pending_since = strategy.pending_since or now.isoformat()
        max_hours = plan.get("max_pending_hours")
        if max_hours is not None and strategy.pending_since is not None:
            started = datetime.fromisoformat(strategy.pending_since)
            if started + timedelta(hours=max_hours) < now:
                self._apply_fail(strategy, "", cause="pending_timeout")
                return
        store.update_strategy(
            strategy.id,
            status="pending",
            pending_since=pending_since,
            unverified_streak=0,
        )

    def _apply_fail(self, strategy, rung, cause: str) -> None:
        store = self._store
        failed = strategy.failed_rounds + 1
        max_rounds = self._current_max_rounds(strategy)
        if failed >= max_rounds:
            totals = store.strategy_spend(strategy.id)
            store.close_strategy(
                strategy.id,
                status="dead",
                death_cause="pending_timeout"
                if cause == "pending_timeout"
                else "round_limit",
                rung_reached=rung,
                failed_rounds=failed,
                money_spent=totals.money,
                tokens=totals.tokens_in + totals.tokens_out,
                hours_to_rung_json="{}",
                closed_at=self._clock.now().isoformat(),
            )
        else:
            store.update_strategy(
                strategy.id, failed_rounds=failed, unverified_streak=0
            )

    def _current_max_rounds(self, strategy) -> int:
        try:
            plan = json.loads(strategy.rungs_json)[strategy.rung_index]
        except (ValueError, IndexError, KeyError):
            return 3
        return plan.get("max_rounds", 3)

    def _apply_unverified(self, strategy) -> None:
        store = self._store
        streak = strategy.unverified_streak + 1
        backoff = list(self._config.verifier.unverified_backoff_minutes)
        wait = backoff[min(streak - 1, len(backoff) - 1)]
        next_check = (self._clock.now() + timedelta(minutes=wait)).isoformat()
        if streak >= 3:
            store.update_strategy(
                strategy.id,
                status="needs_human",
                unverified_streak=streak,
                next_check_at=next_check,
            )
            store.add_event(
                mission=strategy.mission,
                level="warn",
                kind="escalation",
                message=(f"strategy {strategy.id} unverified {streak} times in a row"),
            )
        else:
            store.update_strategy(
                strategy.id,
                unverified_streak=streak,
                next_check_at=next_check,
            )

    def _latest_check_id(self, strategy_id: int) -> int:
        checks = self._store.last_proof_checks(strategy_id, 1)
        return checks[0].id

    def due_strategies(self, mission: str) -> list:
        now_iso = self._clock.now().isoformat()
        due = []
        for strategy in self._store.list_strategies(mission, ("active", "pending")):
            if strategy.next_check_at is None or (strategy.next_check_at <= now_iso):
                due.append(strategy.id)
        return due
