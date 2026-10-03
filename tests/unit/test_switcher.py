from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scavenger.config import load as load_config
from scavenger.goal import (
    BudgetLimit,
    Goal,
    LadderRung,
    StrategyDefaults,
    Target,
)
from scavenger.store import Store
from scavenger.switcher import Candidate, needs_refill, pick, rank

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DEADLINE = datetime(2026, 11, 30, 16, 59, 59, tzinfo=UTC)


def load_example(tmp_path):
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    conf_path = tmp_path / "scavenger.toml"
    conf_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return load_config(conf_path)


def make_store(tmp_path):
    return Store.open(tmp_path / "ledger.sqlite3", run_dir=tmp_path / "run")


def make_goal(ladder=2, amount="1000.00", deadline=None):
    return Goal(
        name="demo",
        created=None,
        statement="rent of 1000 USD due",
        target=Target(
            amount=Decimal(amount),
            currency="USD",
            deadline=deadline or DEADLINE,
        ),
        budget=BudgetLimit(money=Decimal("50.00"), tokens=2000000, rounds=200),
        settle=None,
        ladder=tuple(
            LadderRung(rung=f"rung-{index}", expected_wait_hours=1)
            for index in range(ladder)
        ),
        strategy_defaults=StrategyDefaults(
            max_rounds=3, max_loss=Decimal("10.00"), max_pending_hours=168
        ),
    )


def make_candidate(**overrides):
    fields = {
        "channel": "chan",
        "path": "do thing",
        "path_key": "chan:thing",
        "outward_key": "out:thing",
        "payout_amount": Decimal("100.00"),
        "payout_currency": "USD",
        "guarantor": "escrow",
        "guarantor_evidence": "https://grants.example/1",
        "capital_needed": Decimal(0),
        "hours_first_proof": 1.0,
        "hours_settlement": 24.0,
        "probability": 0.5,
        "probability_note": "estimate",
    }
    fields.update(overrides)
    return Candidate(**fields)


def test_score_formula_matches_spec(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    ranked = rank([make_candidate()], make_goal(), store, config, NOW)
    assert len(ranked) == 1
    item = ranked[0]
    assert item.expected == Decimal("50.00")
    assert item.probability_source == "estimate"
    assert abs(item.score - Decimal(50) / Decimal("0.9")) < Decimal("0.000001")


def test_drop_when_cannot_settle_before_deadline(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    ranked = rank(
        [make_candidate(hours_settlement=24 * 365)],
        make_goal(),
        store,
        config,
        NOW,
    )
    assert ranked == []


def test_drop_below_gap_floor(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    ranked = rank(
        [make_candidate(payout_amount=Decimal("1.00"))],
        make_goal(),
        store,
        config,
        NOW,
    )
    assert ranked == []


def test_guarantor_none_ranked_last(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    backed = make_candidate(path_key="chan:backed", guarantor="escrow")
    bare = make_candidate(path_key="chan:bare", guarantor="none")
    ranked = rank([bare, backed], make_goal(), store, config, NOW)
    assert [item.candidate.path_key for item in ranked] == [
        "chan:backed",
        "chan:bare",
    ]


def test_tie_breaks_deterministic(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    first = make_candidate(path_key="chan:b")
    second = make_candidate(path_key="chan:a")
    ranked = rank([first, second], make_goal(), store, config, NOW)
    assert [item.candidate.path_key for item in ranked] == [
        "chan:a",
        "chan:b",
    ]


def close_strategy(store, channel, outcome, mission="demo"):
    strategy_id = store.add_strategy(
        mission=mission,
        channel=channel,
        path="p",
        path_key=f"{channel}:p",
        outward_key="o",
        payout_amount=Decimal(1),
        payout_currency="USD",
        guarantor="none",
        guarantor_evidence=None,
        capital_needed=Decimal(0),
        hours_first_proof=1.0,
        hours_settlement=1.0,
        probability=0.5,
        probability_source="estimate",
        probability_note="n",
        score=Decimal(1),
        rungs_json="[]",
        created_at="2026-10-03T05:00:00+00:00",
    )
    store.close_strategy(
        strategy_id,
        status="won" if outcome == "won" else "dead",
        death_cause=None if outcome == "won" else "round_limit",
        rung_reached="settled",
        failed_rounds=0,
        money_spent=Decimal(0),
        tokens=0,
        hours_to_rung_json="{}",
        closed_at="2026-10-04T11:00:00+00:00",
    )


def make_mission(store, name="demo"):
    store.create_mission(
        name=name,
        goal_path="missions/demo/GOAL.md",
        goal_sha256="abc",
        status="active",
        target_amount=Decimal("1000.00"),
        target_currency="USD",
        deadline="2026-11-30T16:59:59+00:00",
        created_at="2026-10-03T05:00:00+00:00",
    )


def test_ledger_probability_laplace(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    make_mission(store)
    close_strategy(store, "chan", "won")
    close_strategy(store, "chan", "won")
    close_strategy(store, "chan", "dead")
    ranked = rank([make_candidate(channel="chan")], make_goal(), store, config, NOW)
    assert ranked[0].probability == (2 + 1) / (3 + 2)
    assert ranked[0].probability_source == "ledger"


def test_estimate_probability_clamped(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    high = rank([make_candidate(probability=0.99)], make_goal(), store, config, NOW)[0]
    low = rank([make_candidate(probability=0.0)], make_goal(), store, config, NOW)[0]
    assert high.probability == 0.90
    assert low.probability == 0.01


def test_missing_fx_dropped(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    ranked = rank(
        [make_candidate(payout_currency="EUR")],
        make_goal(),
        store,
        config,
        NOW,
    )
    assert ranked == []


def add_queued(store, outward_key, mission="demo"):
    return store.add_strategy(
        mission=mission,
        channel="chan",
        path=f"path {outward_key}",
        path_key=f"chan:{outward_key}",
        outward_key=outward_key,
        payout_amount=Decimal("100.00"),
        payout_currency="USD",
        guarantor="escrow",
        guarantor_evidence="https://grants.example/1",
        capital_needed=Decimal(0),
        hours_first_proof=1.0,
        hours_settlement=24.0,
        probability=0.5,
        probability_source="estimate",
        probability_note="n",
        score=Decimal(1),
        rungs_json="[]",
        created_at="2026-10-03T05:00:00+00:00",
    )


def test_pick_respects_max_active(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    make_mission(store)
    add_queued(store, "out:a")
    add_queued(store, "out:b")
    picked = pick(store, config, "demo")
    assert len(picked) == 1
    assert store.get_strategy(picked[0]).status == "active"


def test_pick_skips_same_outward_key(tmp_path):
    from dataclasses import replace

    store = make_store(tmp_path)
    config = load_example(tmp_path)
    config = replace(config, loop=replace(config.loop, max_active=2))
    make_mission(store)
    taken = add_queued(store, "out:taken")
    store.update_strategy(taken, status="active")
    other = add_queued(store, "out:other")
    add_queued(store, "out:taken")
    picked = pick(store, config, "demo")
    assert picked == [other]


def test_pending_not_runnable(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    make_mission(store)
    waiting = add_queued(store, "out:w")
    store.update_strategy(waiting, status="pending")
    assert pick(store, config, "demo") == []


def test_needs_refill_respects_interval(tmp_path):
    store = make_store(tmp_path)
    config = load_example(tmp_path)
    make_mission(store)
    assert needs_refill(store, config, "demo") is True
    add_queued(store, "out:live")
    assert needs_refill(store, config, "demo") is False
