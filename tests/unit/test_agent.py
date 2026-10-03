import json
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scavenger.channels import Action
from scavenger.config import ConfigError
from scavenger.config import load as load_config
from scavenger.executor import Executor
from scavenger.outbox import create_outbox
from scavenger.store import Store

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parent.parent / "fixtures" / "fake_agent.py"

GOAL_TEXT = """+++
name = "demo"
statement = "rent of 1500 USD due"

[target]
amount = "1500.00"
currency = "USD"
deadline = 2026-11-30T16:59:59Z

[budget]
money = "50.00"
tokens = 2000000
rounds = 200

[strategy_defaults]
max_rounds = 3
max_loss = "10.00"
+++

Body.
"""


class FakeClock:
    def now(self):
        return NOW


class FakeChannel:
    name = "fake-chan"

    def __init__(self, action):
        self._action = action

    def liveness(self):
        raise NotImplementedError

    def discover(self, goal):
        raise NotImplementedError

    def next_action(self, strategy, last_check):
        return self._action


def local_action():
    return Action(
        kind="local",
        draft=None,
        description="fix the typo",
        fingerprint="fp-local",
    )


def setup(tmp_path, monkeypatch):
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    conf_path = tmp_path / "scavenger.toml"
    conf_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(conf_path)
    monkeypatch.setenv(config.outbox.secret_env, "x" * 32)
    goal_dir = tmp_path / "missions" / "demo"
    goal_dir.mkdir(parents=True)
    (goal_dir / "GOAL.md").write_text(GOAL_TEXT, encoding="utf-8")
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
    return config, store


def add_strategy(store):
    return store.add_strategy(
        mission="demo",
        channel="fake-chan",
        path="p",
        path_key="fake-chan:p",
        outward_key="github:o/r",
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


def with_agent_command(config, args):
    from dataclasses import replace

    return replace(config, executor=replace(config.executor, agent_command=tuple(args)))


def test_agent_timeout_fails_round(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    config = with_agent_command(config, [sys.executable, str(FIXTURE), "sleep"])
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    executor = Executor(
        store,
        config,
        {"fake-chan": FakeChannel(local_action())},
        outbox,
        FakeClock(),
    )

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(subprocess, "run", raise_timeout)
    assert executor.run_round(strategy_id) == "agent-failed"
    rounds = store.list_rounds(strategy_id)
    assert len(rounds) == 1
    assert rounds[0].result_state == "fail"


def test_missing_patch_fails_round(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    config = with_agent_command(config, [sys.executable, str(FIXTURE), "nopatch"])
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    executor = Executor(
        store,
        config,
        {"fake-chan": FakeChannel(local_action())},
        outbox,
        FakeClock(),
    )
    assert executor.run_round(strategy_id) == "agent-failed"
    assert store.list_rounds(strategy_id)[0].result_state == "fail"


def test_patch_becomes_pr_draft(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    config = with_agent_command(config, [sys.executable, str(FIXTURE), "ok"])
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    executor = Executor(
        store,
        config,
        {"fake-chan": FakeChannel(local_action())},
        outbox,
        FakeClock(),
    )
    assert executor.run_round(strategy_id) == "agent-drafted"
    drafts = store.list_outbox("awaiting")
    assert len(drafts) == 1
    assert drafts[0].kind == "github_pr"
    payload = json.loads(drafts[0].payload_json)
    assert payload["repo"] == "o/r"
    assert Path(payload["patch_path"]).exists()


def test_agent_runs_in_workspace_dir(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    config = with_agent_command(config, [sys.executable, str(FIXTURE), "ok"])
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    executor = Executor(
        store,
        config,
        {"fake-chan": FakeChannel(local_action())},
        outbox,
        FakeClock(),
    )
    executor.run_round(strategy_id)
    workdir = tmp_path / "missions" / "demo" / "work" / str(strategy_id)
    assert (workdir / "out" / "patch.diff").exists()


def test_empty_agent_command_raises(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    executor = Executor(
        store,
        config,
        {"fake-chan": FakeChannel(local_action())},
        outbox,
        FakeClock(),
    )
    try:
        executor.run_round(strategy_id)
    except ConfigError:
        return
    raise AssertionError("expected ConfigError")
