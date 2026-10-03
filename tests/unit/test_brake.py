from datetime import UTC, datetime
from decimal import Decimal

from scavenger.brake import mission_verdict, strategy_verdict
from scavenger.goal import BudgetLimit, Goal, StrategyDefaults, Target
from scavenger.store import Store

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
FUTURE = datetime(2026, 11, 30, 16, 59, 59, tzinfo=UTC)


def make_goal(**overrides):
    fields = {
        "name": "demo",
        "created": None,
        "statement": "rent of 1500 USD due 2026-11-30",
        "target": Target(amount=Decimal("1500.00"), currency="USD", deadline=FUTURE),
        "budget": BudgetLimit(money=Decimal("50.00"), tokens=2000000, rounds=200),
        "settle": None,
        "strategy_defaults": StrategyDefaults(
            max_rounds=3, max_loss=Decimal("10.00"), max_pending_hours=168
        ),
    }
    fields.update(overrides)
    return Goal(**fields)


def make_store(tmp_path):
    return Store.open(tmp_path / "ledger.sqlite3", run_dir=tmp_path / "run")


def make_mission(store, name="demo"):
    store.create_mission(
        name=name,
        goal_path="missions/demo/GOAL.md",
        goal_sha256="abc",
        status="active",
        target_amount=Decimal("1500.00"),
        target_currency="USD",
        deadline="2026-11-30T16:59:59+00:00",
        created_at="2026-10-03T05:00:00+00:00",
    )


def make_strategy(store, **overrides):
    fields = {
        "mission": "demo",
        "channel": "test-channel",
        "path": "thing",
        "path_key": "test-channel:thing",
        "outward_key": "test:thing",
        "payout_amount": Decimal("100.00"),
        "payout_currency": "USD",
        "guarantor": "none",
        "guarantor_evidence": None,
        "capital_needed": Decimal(0),
        "hours_first_proof": 1.0,
        "hours_settlement": 24.0,
        "probability": 0.5,
        "probability_source": "estimate",
        "probability_note": "n",
        "score": Decimal(1),
        "rungs_json": "[]",
        "created_at": "2026-10-03T05:00:00+00:00",
    }
    fields.update(overrides)
    return store.add_strategy(**fields)


def settle(store, strategy_id, amount, message_id):
    round_id = store.start_round(
        strategy_id,
        started_at="2026-10-04T11:00:00+00:00",
        action_kind="email",
        action_fingerprint="fp",
    )
    check_id = store.add_proof_check(
        strategy_id,
        round_id,
        "settled",
        "payment_email",
        "{}",
        "pass",
        "raw",
        None,
        "2026-10-04T11:30:00+00:00",
    )
    assert store.add_settlement(
        mission="demo",
        strategy_id=strategy_id,
        amount=amount,
        currency="USD",
        amount_in_target=amount,
        message_id=message_id,
        proof_check_id=check_id,
        at="2026-10-04T11:30:00+00:00",
    )


def test_strategy_round_limit(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    store.update_strategy(strategy_id, failed_rounds=3)
    verdict = strategy_verdict(store.get_strategy(strategy_id), make_goal())
    assert verdict is not None and verdict.cause == "round_limit"


def test_strategy_loss_limit(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    store.update_strategy(strategy_id, loss=Decimal("10.00"))
    verdict = strategy_verdict(store.get_strategy(strategy_id), make_goal())
    assert verdict is not None and verdict.cause == "loss_limit"


def test_strategy_no_verdict(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    assert strategy_verdict(store.get_strategy(strategy_id), make_goal()) is None


def test_mission_done(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    settle(store, strategy_id, Decimal("1500.00"), "m-done")
    assert (
        mission_verdict(store.get_mission("demo"), make_goal(), store, NOW).stop
        == "done"
    )


def test_mission_deadline(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    late = datetime(2026, 12, 1, tzinfo=UTC)
    assert (
        mission_verdict(store.get_mission("demo"), make_goal(), store, late).stop
        == "stopped_deadline"
    )


def test_budget_counts_llm_and_capital_spend(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    store.add_spend(
        mission="demo",
        strategy_id=None,
        at="2026-10-04T11:00:00+00:00",
        tokens_in=10,
        tokens_out=10,
        money=Decimal("30.00"),
        source="llm",
    )
    store.add_spend(
        mission="demo",
        strategy_id=None,
        at="2026-10-04T11:00:00+00:00",
        tokens_in=0,
        tokens_out=0,
        money=Decimal("25.00"),
        source="capital",
    )
    assert (
        mission_verdict(store.get_mission("demo"), make_goal(), store, NOW).stop
        == "stopped_budget"
    )


def test_mission_budget_tokens(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    store.add_spend(
        mission="demo",
        strategy_id=None,
        at="2026-10-04T11:00:00+00:00",
        tokens_in=1500000,
        tokens_out=600000,
        money=Decimal("1.00"),
        source="llm",
    )
    assert (
        mission_verdict(store.get_mission("demo"), make_goal(), store, NOW).stop
        == "stopped_budget"
    )


def test_mission_budget_rounds(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    store.update_strategy(strategy_id, failed_rounds=200)
    assert (
        mission_verdict(store.get_mission("demo"), make_goal(), store, NOW).stop
        == "stopped_budget"
    )


def test_mission_impossible(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    store.close_strategy(
        strategy_id,
        status="dead",
        death_cause="round_limit",
        rung_reached="submitted",
        failed_rounds=3,
        money_spent=Decimal(1),
        tokens=10,
        hours_to_rung_json="{}",
        closed_at=NOW.isoformat(),
    )
    with store.tx():
        store.tx().execute("UPDATE missions SET empty_refills = 2 WHERE name = 'demo'")
    assert (
        mission_verdict(store.get_mission("demo"), make_goal(), store, NOW).stop
        == "impossible"
    )


def test_done_wins_over_deadline_same_tick(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    settle(store, strategy_id, Decimal("1500.00"), "m-tie")
    late = datetime(2026, 12, 1, tzinfo=UTC)
    assert (
        mission_verdict(store.get_mission("demo"), make_goal(), store, late).stop
        == "done"
    )


def test_no_verdict_when_healthy(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    make_strategy(store)
    assert mission_verdict(store.get_mission("demo"), make_goal(), store, NOW) is None
