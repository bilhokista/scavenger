import inspect
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scavenger.adapters import ProofResult, ProofState
from scavenger.config import load as load_config
from scavenger.store import Store
from scavenger.verifier import Verifier

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

RUNGS = [
    {
        "rung": "submitted",
        "adapter": "fake",
        "locator": {"repo": "o/r"},
        "max_rounds": 2,
        "max_pending_hours": 72,
    },
    {
        "rung": "settled",
        "adapter": "fake",
        "locator": {"repo": "o/r"},
        "max_rounds": 2,
        "max_pending_hours": 72,
    },
]


class FakeClock:
    def __init__(self, moment):
        self._moment = moment

    def now(self):
        return self._moment


class FakeAdapter:
    name = "fake"

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def check(self, rung, locator, since):
        self.calls.append((rung, dict(locator), since))
        return self._results.pop(0)


def passing():
    return ProofResult(state=ProofState.PASS, evidence_raw="ok", detail="ok")


def pending():
    return ProofResult(state=ProofState.PENDING, evidence_raw="", detail="wait")


def failing():
    return ProofResult(state=ProofState.FAIL, evidence_raw="bad", detail="bad")


def unverified():
    return ProofResult(state=ProofState.UNVERIFIED, evidence_raw="", detail="down")


def setup(tmp_path, adapter_results, rungs=None):
    from pathlib import Path

    example = Path(__file__).parents[2] / "scavenger.example.toml"
    conf_path = tmp_path / "scavenger.toml"
    conf_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(conf_path)
    store = Store.open(tmp_path / "ledger.sqlite3", run_dir=tmp_path / "run")
    store.create_mission(
        name="demo",
        goal_path="missions/demo/GOAL.md",
        goal_sha256="abc",
        status="active",
        target_amount=Decimal("1500.00"),
        target_currency="USD",
        deadline="2026-11-30T16:59:59+00:00",
        created_at="2026-10-03T05:00:00+00:00",
    )
    strategy_id = store.add_strategy(
        mission="demo",
        channel="test-channel",
        path="thing",
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
        probability_note="n",
        score=Decimal(1),
        rungs_json=json.dumps(rungs if rungs is not None else RUNGS),
        status="active",
        created_at="2026-10-03T05:00:00+00:00",
    )
    adapter = FakeAdapter(adapter_results)
    verifier = Verifier(store, config, {"fake": adapter}, FakeClock(NOW))
    return store, verifier, adapter, strategy_id


def start_round_with_locator(store, strategy_id, locator, started_at=None):
    return store.start_round(
        strategy_id,
        started_at=started_at or "2026-10-04T11:00:00+00:00",
        action_kind="draft",
        action_fingerprint="fp",
        locator_json=json.dumps(locator),
    )


def test_pass_climbs_rung(tmp_path):
    store, verifier, _adapter, strategy_id = setup(tmp_path, [passing()])
    start_round_with_locator(store, strategy_id, {"pr": 12})
    result = verifier.check_strategy(strategy_id)
    assert result.state == ProofState.PASS
    assert store.get_strategy(strategy_id).rung_index == 1


def test_pass_on_last_rung_wins_and_writes_ledger(tmp_path):
    store, verifier, _adapter, strategy_id = setup(tmp_path, [passing()])
    store.update_strategy(strategy_id, rung_index=1)
    start_round_with_locator(store, strategy_id, {"pr": 12})
    verifier.check_strategy(strategy_id)
    strategy = store.get_strategy(strategy_id)
    assert strategy.status == "won"
    rows = store.ledger_for_channel("test-channel")
    assert len(rows) == 1
    assert rows[0].outcome == "won"


def test_fail_increments_and_kills_at_limit(tmp_path):
    store, verifier, _adapter, strategy_id = setup(tmp_path, [failing(), failing()])
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    assert store.get_strategy(strategy_id).failed_rounds == 1
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    strategy = store.get_strategy(strategy_id)
    assert strategy.status == "dead"
    assert strategy.death_cause == "round_limit"


def test_pending_sets_pending_since_once(tmp_path):
    store, verifier, _adapter, strategy_id = setup(tmp_path, [pending(), pending()])
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    first = store.get_strategy(strategy_id).pending_since
    assert first is not None
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    assert store.get_strategy(strategy_id).pending_since == first


def test_pending_timeout_counts_as_fail(tmp_path):
    store, verifier, _adapter, strategy_id = setup(tmp_path, [pending()])
    store.update_strategy(
        strategy_id, pending_since="2026-10-01T00:00:00+00:00", status="pending"
    )
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    assert store.get_strategy(strategy_id).failed_rounds == 1


def test_unverified_backoff_schedule(tmp_path):
    store, verifier, _adapter, strategy_id = setup(tmp_path, [unverified()])
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    strategy = store.get_strategy(strategy_id)
    assert strategy.unverified_streak == 1
    assert strategy.next_check_at == (NOW + timedelta(minutes=5)).isoformat()


def test_third_unverified_moves_to_needs_human_and_notifies(tmp_path):
    store, verifier, _adapter, strategy_id = setup(
        tmp_path, [unverified(), unverified(), unverified()]
    )
    for _ in range(3):
        start_round_with_locator(store, strategy_id, {})
        verifier.check_strategy(strategy_id)
    strategy = store.get_strategy(strategy_id)
    assert strategy.status == "needs_human"
    events = store._conn.execute(
        "SELECT * FROM events WHERE kind = 'escalation'"
    ).fetchall()
    assert len(events) == 1


def test_settlement_recorded_with_fx(tmp_path):
    from scavenger.adapters import Settlement

    paid = ProofResult(
        state=ProofState.PASS,
        evidence_raw="mail",
        detail="paid",
        settlement=Settlement(
            amount=Decimal(160000), currency="IDR", message_id="m-fx"
        ),
    )
    rungs = [dict(RUNGS[0], max_rounds=5), dict(RUNGS[1], max_rounds=5)]
    store, verifier, _adapter, strategy_id = setup(tmp_path, [paid], rungs=rungs)
    store.update_strategy(strategy_id, rung_index=1)
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    rows = store._conn.execute("SELECT * FROM settlements").fetchall()
    assert len(rows) == 1
    assert rows[0]["message_id"] == "m-fx"


def test_settlement_without_fx_recorded_null(tmp_path):
    from scavenger.adapters import Settlement

    paid = ProofResult(
        state=ProofState.PASS,
        evidence_raw="mail",
        detail="paid",
        settlement=Settlement(amount=Decimal(10), currency="EUR", message_id="m-nofx"),
    )
    store, verifier, _adapter, strategy_id = setup(tmp_path, [paid])
    store.update_strategy(strategy_id, rung_index=1)
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    row = store._conn.execute("SELECT * FROM settlements").fetchone()
    assert row["amount_in_target"] is None


def test_duplicate_settlement_treated_as_pending(tmp_path):
    from scavenger.adapters import Settlement

    def paid():
        return ProofResult(
            state=ProofState.PASS,
            evidence_raw="mail",
            detail="paid",
            settlement=Settlement(
                amount=Decimal(10), currency="USD", message_id="m-dup"
            ),
        )

    store, verifier, _adapter, strategy_id = setup(
        tmp_path,
        [paid(), paid()],
        rungs=[dict(RUNGS[0], max_rounds=5), dict(RUNGS[1], max_rounds=5)],
    )
    store.update_strategy(strategy_id, rung_index=1)
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    start_round_with_locator(store, strategy_id, {})
    verifier.check_strategy(strategy_id)
    assert (
        store._conn.execute("SELECT COUNT(*) AS n FROM settlements").fetchone()["n"]
        == 1
    )
    assert store.get_strategy(strategy_id).status in ("pending", "active")
    warns = store._conn.execute("SELECT * FROM events WHERE level = 'warn'").fetchall()
    assert warns


def test_verifier_signature_has_no_executor_input():
    params = inspect.signature(Verifier.check_strategy).parameters
    assert "executor" not in params
    assert list(params) == ["self", "strategy_id"]


def test_adapter_receives_only_template_and_round_locator(tmp_path):
    store, verifier, _adapter, strategy_id = setup(tmp_path, [passing()])
    start_round_with_locator(store, strategy_id, {"pr": 12})
    verifier.check_strategy(strategy_id)
    rung, locator, _since = _adapter.calls[0]
    assert rung == "submitted"
    assert locator == {"repo": "o/r", "pr": 12}
