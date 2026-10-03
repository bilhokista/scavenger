from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scavenger.channels import Action
from scavenger.config import load as load_config
from scavenger.executor import Executor
from scavenger.outbox import DraftSpec, create_outbox
from scavenger.store import Store

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

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

    def __init__(self, actions):
        self._actions = list(actions)
        self.calls = 0

    def liveness(self):
        raise NotImplementedError

    def discover(self, goal):
        raise NotImplementedError

    def next_action(self, strategy, last_check):
        self.calls += 1
        return self._actions.pop(0)


def draft_action():
    return Action(
        kind="draft",
        draft=DraftSpec(kind="email", target="a@b.c", body="hi"),
        description="send intro",
        fingerprint="fp-1",
        tokens_in=10,
        tokens_out=20,
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
        outward_key="o",
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


def test_round_skipped_while_awaiting_approval(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    outbox.create_draft(
        DraftSpec(kind="email", target="a@b.c", body="hi"),
        "demo",
        strategy_id,
    )
    channel = FakeChannel([draft_action()])
    executor = Executor(store, config, {"fake-chan": channel}, outbox, FakeClock())
    assert executor.run_round(strategy_id) == "skipped:open-outbox"
    assert channel.calls == 0
    assert store.list_rounds(strategy_id) == []


def test_draft_action_creates_outbox_not_send(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    executor = Executor(
        store,
        config,
        {"fake-chan": FakeChannel([draft_action()])},
        outbox,
        FakeClock(),
    )
    assert executor.run_round(strategy_id) == "drafted"
    assert len(store.list_outbox("awaiting")) == 1
    assert store.list_outbox("sent") == []


def test_dead_strategy_not_executed(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    store.update_strategy(strategy_id, failed_rounds=3)
    channel = FakeChannel([draft_action()])
    executor = Executor(store, config, {"fake-chan": channel}, outbox, FakeClock())
    assert executor.run_round(strategy_id) == "dead:round_limit"
    assert channel.calls == 0
    assert store.get_strategy(strategy_id).status == "dead"


def test_draft_links_round_to_outbox(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    executor = Executor(
        store,
        config,
        {"fake-chan": FakeChannel([draft_action()])},
        outbox,
        FakeClock(),
    )
    executor.run_round(strategy_id)
    item = store.list_outbox("awaiting")[0]
    round_row = store.latest_round(strategy_id)
    assert round_row.outbox_id == item.id
    assert store.round_for_outbox(item.id).id == round_row.id


def test_spend_recorded(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    strategy_id = add_strategy(store)
    executor = Executor(
        store,
        config,
        {"fake-chan": FakeChannel([draft_action()])},
        outbox,
        FakeClock(),
    )
    executor.run_round(strategy_id)
    total = store.spend_since("demo", "0001-01-01T00:00:00+00:00")
    assert (total.tokens_in, total.tokens_out) == (10, 20)
