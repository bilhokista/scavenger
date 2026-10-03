from decimal import Decimal
from pathlib import Path

from scavenger.report import render_report, write_report
from scavenger.store import Store

GOAL_TEXT = """+++
name = "demo"
statement = "rent of 1500 USD due"

[[assumptions]]
text = "Currency is USD."
source = "silence"
+++

## Statement

Body text.
"""


def make_store(tmp_path):
    return Store.open(tmp_path / "ledger.sqlite3", run_dir=tmp_path / "run")


def seed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    goal_dir = tmp_path / "missions" / "demo"
    goal_dir.mkdir(parents=True)
    (goal_dir / "GOAL.md").write_text(GOAL_TEXT, encoding="utf-8")
    store = make_store(tmp_path)
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
    first = store.add_strategy(
        mission="demo",
        channel="chan-a",
        path="first thing",
        path_key="chan-a:first",
        outward_key="x:a",
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
        rungs_json="[]",
        created_at="2026-10-03T05:00:00+00:00",
    )
    second = store.add_strategy(
        mission="demo",
        channel="chan-b",
        path="second thing",
        path_key="chan-b:second",
        outward_key="x:b",
        payout_amount=Decimal("200.00"),
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
        rungs_json="[]",
        created_at="2026-10-03T05:00:00+00:00",
    )
    return store, first, second


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


def test_report_contains_settled_vs_target(tmp_path, monkeypatch):
    store, first, _ = seed(tmp_path, monkeypatch)
    settle(store, first, Decimal("750.00"), "m-half")
    report = render_report(store, "demo")
    assert "750.00 / 1500.00 USD" in report
    assert "50.0%" in report


def test_report_lists_every_strategy(tmp_path, monkeypatch):
    store, _, _ = seed(tmp_path, monkeypatch)
    report = render_report(store, "demo")
    assert "first thing" in report
    assert "second thing" in report
    assert "Currency is USD." in report


def test_report_marks_failed_mission_when_zero_settled(tmp_path, monkeypatch):
    store, _, _ = seed(tmp_path, monkeypatch)
    report = render_report(store, "demo")
    assert "FAILED" in report


def test_report_snapshot(tmp_path, monkeypatch):
    store, first, _ = seed(tmp_path, monkeypatch)
    settle(store, first, Decimal("750.00"), "m-half")
    store.add_spend(
        mission="demo",
        strategy_id=first,
        at="2026-10-04T11:00:00+00:00",
        tokens_in=100,
        tokens_out=50,
        money=Decimal("1.00"),
        source="llm",
    )
    store.block_channel("chan-b", "round_limit", "2026-10-20T00:00:00+00:00")
    report = render_report(store, "demo")
    fixture = Path(__file__).parent.parent / "fixtures" / "report_expected.md"
    assert report == fixture.read_text(encoding="utf-8")


def test_write_report(tmp_path, monkeypatch):
    store, _, _ = seed(tmp_path, monkeypatch)
    path = write_report(store, "demo", tmp_path)
    assert path == tmp_path / "demo" / "REPORT.md"
    assert path.exists()
