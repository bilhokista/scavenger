import json
from decimal import Decimal
from pathlib import Path

from scavenger import cli
from scavenger.llm import LLMResult

from tests.conftest import FakeLLM

VALID_DRAFT = {
    "name": "demo",
    "statement": "rent of 1500 USD due 2026-11-30",
    "target": {
        "amount": "1500.00",
        "currency": "USD",
        "deadline": "2026-12-31T00:00:00+00:00",
    },
    "budget": {"money": "50.00", "tokens": 2000000, "rounds": 200},
    "settle": {"adapter": "payment_email", "rules": ["example-bank"]},
    "ladder": [
        {"rung": "submitted", "expected_wait_hours": 2},
        {"rung": "replied", "expected_wait_hours": 72},
        {"rung": "settled", "expected_wait_hours": 336},
    ],
    "strategy_defaults": {
        "max_rounds": 3,
        "max_loss": "10.00",
        "max_pending_hours": 168,
    },
}


def make_config(tmp_path, monkeypatch):
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    conf_path = tmp_path / "scavenger.toml"
    conf_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("SCAVENGER_APPROVAL_SECRET", "x" * 32)
    return str(conf_path)


def make_llm(monkeypatch):
    fake = FakeLLM([LLMResult(text=json.dumps(VALID_DRAFT), tokens_in=1, tokens_out=1)])
    monkeypatch.setattr(cli, "build_llm", lambda config: fake)
    return fake


def run(argv):
    return cli.main(argv)


def test_init(tmp_path):
    conf = str(tmp_path / "scavenger.toml")
    assert run(["--config", conf, "init"]) == 0
    assert Path(conf).exists()
    assert (tmp_path / "missions").exists()


def test_compile(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    assert run(["--config", conf, "compile", "cari duit"]) == 0
    assert (tmp_path / "missions" / "demo" / "GOAL.md").exists()


def test_validate(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    assert run(["--config", conf, "validate", "demo"]) == 0


def test_run_once(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    assert run(["--config", conf, "run", "--once"]) == 0


def test_status(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    assert run(["--config", conf, "status", "demo"]) == 0


def make_draft(conf):
    from scavenger import clock
    from scavenger.config import load
    from scavenger.outbox import DraftSpec, create_outbox
    from scavenger.store import Store

    config = load(conf)
    store = Store.open(config.paths.db, run_dir=config.paths.run_dir)
    outbox = create_outbox(store, config, [], clock)
    return store, outbox.create_draft(
        DraftSpec(kind="email", target="a@b.c", body="hi"), "demo", None
    )


def test_approve(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    _, item = make_draft(conf)
    assert run(["--config", conf, "approve", item.id]) == 0


def test_reject(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    _, item = make_draft(conf)
    assert run(["--config", conf, "reject", item.id]) == 0


def test_redraft(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    _store, item = make_draft(conf)
    from scavenger.config import load

    config = load(conf)
    path = config.paths.missions / "demo" / "outbox" / f"{item.id}.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("hi", "hi v2"), encoding="utf-8")
    assert run(["--config", conf, "redraft", item.id]) == 0


def test_mark_sent(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    from scavenger import clock
    from scavenger.config import load
    from scavenger.outbox import DraftSpec, create_outbox
    from scavenger.store import Store

    config = load(conf)
    store = Store.open(config.paths.db, run_dir=config.paths.run_dir)
    outbox = create_outbox(store, config, [], clock)
    item = outbox.create_draft(
        DraftSpec(kind="manual", target="form", body="fill"), "demo", None
    )
    outbox.approve(item.id, None, via="cli", by="human")
    assert (
        run(
            [
                "--config",
                conf,
                "mark-sent",
                item.id,
                "--locator",
                '{"application_url": "https://x.example/1"}',
            ]
        )
        == 0
    )


def add_strategy(conf):
    from scavenger.config import load
    from scavenger.store import Store

    config = load(conf)
    store = Store.open(config.paths.db, run_dir=config.paths.run_dir)
    return store.add_strategy(
        mission="demo",
        channel="chan",
        path="p",
        path_key="chan:p",
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
        created_at="2026-10-04T12:00:00+00:00",
    )


def test_guarantor(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    strategy_id = add_strategy(conf)
    assert (
        run(
            [
                "--config",
                conf,
                "guarantor",
                str(strategy_id),
                "signed_client",
                "https://sign.example/1",
            ]
        )
        == 0
    )


def test_resume(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    strategy_id = add_strategy(conf)
    from scavenger.config import load
    from scavenger.store import Store

    config = load(conf)
    store = Store.open(config.paths.db, run_dir=config.paths.run_dir)
    store.update_strategy(strategy_id, status="needs_human")
    assert run(["--config", conf, "resume", str(strategy_id)]) == 0


def test_stop(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    assert run(["--config", conf, "stop", "demo"]) == 0


def test_report(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    assert run(["--config", conf, "report", "demo"]) == 0
    assert (tmp_path / "missions" / "demo" / "REPORT.md").exists()


def test_supervise_once(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    make_llm(monkeypatch)
    run(["--config", conf, "compile", "cari duit"])
    from scavenger.config import load
    from scavenger.store import Store

    config = load(conf)
    store = Store.open(config.paths.db, run_dir=config.paths.run_dir)
    store.write_heartbeat()
    assert run(["--config", conf, "supervise", "--once"]) == 0


def test_liveness(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    assert run(["--config", conf, "liveness"]) == 0


def test_unblock(tmp_path, monkeypatch):
    conf = make_config(tmp_path, monkeypatch)
    assert run(["--config", conf, "unblock", "chan"]) == 0
