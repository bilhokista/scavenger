import sqlite3
from decimal import Decimal

import pytest
from scavenger.store import Store


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


def make_strategy(store, mission="demo", channel="test-channel"):
    return store.add_strategy(
        mission=mission,
        channel=channel,
        path="do a test thing",
        path_key="test-channel:thing",
        outward_key="test:thing",
        payout_amount=Decimal("100.00"),
        payout_currency="USD",
        guarantor="none",
        guarantor_evidence=None,
        capital_needed=Decimal(0),
        hours_first_proof=1.0,
        hours_settlement=24.0,
        probability=0.5,
        probability_source="estimate",
        probability_note="test",
        score=Decimal(10),
        rungs_json="[]",
        created_at="2026-10-03T05:00:00+00:00",
    )


def test_migrations_create_all_tables(tmp_path):
    store = make_store(tmp_path)
    tables = {
        row[0]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    for expected in (
        "schema_version",
        "missions",
        "strategies",
        "rounds",
        "proof_checks",
        "outbox",
        "spend",
        "settlements",
        "ledger",
        "channel_blocks",
        "events",
    ):
        assert expected in tables


def test_reopen_does_not_reapply_migrations(tmp_path):
    make_store(tmp_path).close()
    second = make_store(tmp_path)
    version = second._conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == 1
    count = second._conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count == 1


def test_foreign_keys_enforced(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.add_strategy(
            mission="no-such-mission",
            channel="c",
            path="p",
            path_key="c:p",
            outward_key="x",
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


def test_money_roundtrips_as_decimal(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    strategy = store.get_strategy(strategy_id)
    assert strategy.payout_amount == Decimal("100.00")
    assert isinstance(strategy.payout_amount, Decimal)
    assert isinstance(strategy.loss, Decimal)


def test_close_strategy_writes_ledger_atomically(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    real_conn = store._conn

    class FailConn:
        def __init__(self, conn):
            self._conn = conn

        def _failing_execute(self, sql, *args, **kwargs):
            if "INSERT INTO ledger" in sql:
                raise RuntimeError("injected")
            return self._conn.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            if name == "execute":
                return self._failing_execute
            return getattr(self._conn, name)

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *exc):
            return self._conn.__exit__(*exc)

    store._conn = FailConn(real_conn)
    with pytest.raises(RuntimeError):
        store.close_strategy(
            strategy_id,
            status="dead",
            death_cause="round_limit",
            rung_reached="submitted",
            failed_rounds=3,
            money_spent=Decimal("1.00"),
            tokens=100,
            hours_to_rung_json="{}",
            closed_at="2026-10-03T06:00:00+00:00",
        )
    strategy = store.get_strategy(strategy_id)
    assert strategy.status == "queued"
    assert store.ledger_for_channel("test-channel") == []


def test_duplicate_settlement_message_id_rejected(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    strategy_id = make_strategy(store)
    round_id = store.start_round(
        strategy_id,
        started_at="2026-10-03T05:00:00+00:00",
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
        "2026-10-03T06:00:00+00:00",
    )
    assert (
        store.add_settlement(
            mission="demo",
            strategy_id=strategy_id,
            amount=Decimal("10.00"),
            currency="USD",
            amount_in_target=Decimal("10.00"),
            message_id="msg-1",
            proof_check_id=check_id,
            at="2026-10-03T06:00:00+00:00",
        )
        is True
    )
    assert (
        store.add_settlement(
            mission="demo",
            strategy_id=strategy_id,
            amount=Decimal("10.00"),
            currency="USD",
            amount_in_target=Decimal("10.00"),
            message_id="msg-1",
            proof_check_id=check_id,
            at="2026-10-03T06:00:00+00:00",
        )
        is False
    )


def test_settled_in_target_ignores_null_fx_rows(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    assert store.settled_in_target("demo") == Decimal(0)
    strategy_id = make_strategy(store)
    round_id = store.start_round(
        strategy_id,
        started_at="2026-10-03T05:00:00+00:00",
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
        "2026-10-03T06:00:00+00:00",
    )
    store.add_settlement(
        mission="demo",
        strategy_id=strategy_id,
        amount=Decimal("5.00"),
        currency="IDR",
        amount_in_target=None,
        message_id="m1",
        proof_check_id=check_id,
        at="2026-10-03T06:00:00+00:00",
    )
    store.add_settlement(
        mission="demo",
        strategy_id=strategy_id,
        amount=Decimal("10.00"),
        currency="USD",
        amount_in_target=Decimal("10.00"),
        message_id="m2",
        proof_check_id=check_id,
        at="2026-10-03T06:00:00+00:00",
    )
    assert store.settled_in_target("demo") == Decimal("10.00")


def test_spend_since_filters_by_time(tmp_path):
    store = make_store(tmp_path)
    make_mission(store)
    store.add_spend(
        mission="demo",
        strategy_id=None,
        at="2026-10-01T00:00:00+00:00",
        tokens_in=10,
        tokens_out=10,
        money=Decimal("1.00"),
        source="llm",
    )
    store.add_spend(
        mission="demo",
        strategy_id=None,
        at="2026-10-03T05:00:00+00:00",
        tokens_in=20,
        tokens_out=30,
        money=Decimal("2.00"),
        source="llm",
    )
    total = store.spend_since("demo", "2026-10-02T00:00:00+00:00")
    assert total.money == Decimal("2.00")
    assert (total.tokens_in, total.tokens_out) == (20, 30)
