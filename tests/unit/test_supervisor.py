import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from scavenger import supervisor
from scavenger.config import load as load_config
from scavenger.store import Store

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class FakeClock:
    def now(self):
        return NOW


class FakeNotifier:
    name = "fake"

    def __init__(self):
        self.notices = []

    def notify(self, notice):
        self.notices.append(notice)


def setup(tmp_path, monkeypatch):
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    conf_path = tmp_path / "scavenger.toml"
    conf_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(conf_path)
    monkeypatch.setattr(supervisor, "clock", FakeClock())
    store = Store.open(config.paths.db, run_dir=config.paths.run_dir)
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


def add_round(store, strategy_id, fingerprint="fp", state="fail"):
    round_id = store.start_round(
        strategy_id,
        started_at="2026-10-04T11:00:00+00:00",
        action_kind="email",
        action_fingerprint=fingerprint,
    )
    store.end_round(round_id, ended_at="2026-10-04T11:01:00+00:00", result_state=state)


def write_heartbeat(config, moment):
    run_dir = config.paths.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "heartbeat").write_text(moment.isoformat(), encoding="utf-8")


def test_supervisor_imports_are_restricted():
    source = Path(supervisor.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {"config", "clock", "store", "report", "notifiers", "money"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("scavenger"):
                    imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "scavenger":
                imported.update(f"scavenger.{alias.name}" for alias in node.names)
            elif node.module and node.module.startswith("scavenger."):
                imported.add(node.module)
    foreign = {
        name.split(".")[1]
        for name in imported
        if name != "scavenger" and name.split(".")[1] not in allowed
    }
    assert not foreign, foreign


def test_quiet_when_all_normal(tmp_path, monkeypatch):
    config, _store = setup(tmp_path, monkeypatch)
    write_heartbeat(config, NOW)
    notifier = FakeNotifier()
    result = supervisor.run_once(config, notifiers=[notifier])
    assert result.exit_code == 0
    assert not result.acted
    assert notifier.notices == []


def test_hourly_money_trigger(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    write_heartbeat(config, NOW)
    store.add_spend(
        mission="demo",
        strategy_id=None,
        at="2026-10-04T11:30:00+00:00",
        tokens_in=0,
        tokens_out=0,
        money=Decimal("6.00"),
        source="llm",
    )
    notifier = FakeNotifier()
    result = supervisor.run_once(config, notifiers=[notifier])
    assert result.exit_code == 3
    assert store.get_mission("demo").status == "stopped_emergency"
    assert notifier.notices


def test_hourly_token_trigger(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    write_heartbeat(config, NOW)
    store.add_spend(
        mission="demo",
        strategy_id=None,
        at="2026-10-04T11:30:00+00:00",
        tokens_in=400000,
        tokens_out=200000,
        money=Decimal(0),
        source="llm",
    )
    result = supervisor.run_once(config, notifiers=[FakeNotifier()])
    assert result.exit_code == 3
    assert store.get_mission("demo").status == "stopped_emergency"


def test_spin_trigger_same_fingerprint_same_state(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    write_heartbeat(config, NOW)
    strategy_id = add_strategy(store)
    add_round(store, strategy_id)
    add_round(store, strategy_id)
    add_round(store, strategy_id)
    result = supervisor.run_once(config, notifiers=[FakeNotifier()])
    assert result.exit_code == 3
    assert store.get_mission("demo").status == "stopped_emergency"


def test_pending_rounds_do_not_count_as_spin(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    write_heartbeat(config, NOW)
    strategy_id = add_strategy(store)
    add_round(store, strategy_id, state="pending")
    add_round(store, strategy_id, state="pending")
    add_round(store, strategy_id, state="pending")
    result = supervisor.run_once(config, notifiers=[FakeNotifier()])
    assert result.exit_code == 0


def test_emergency_writes_report_and_notifies(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    write_heartbeat(config, NOW)
    store.add_spend(
        mission="demo",
        strategy_id=None,
        at="2026-10-04T11:30:00+00:00",
        tokens_in=0,
        tokens_out=0,
        money=Decimal("99.00"),
        source="llm",
    )
    notifier = FakeNotifier()
    supervisor.run_once(config, notifiers=[notifier])
    assert (config.paths.missions / "demo" / "REPORT.md").exists()
    assert any(n.kind == "emergency_stop" for n in notifier.notices)


def test_stale_heartbeat_notifies(tmp_path, monkeypatch):
    config, _store = setup(tmp_path, monkeypatch)
    write_heartbeat(config, NOW - timedelta(hours=2))
    notifier = FakeNotifier()
    result = supervisor.run_once(config, notifiers=[notifier])
    assert any("heartbeat" in n.title.lower() for n in notifier.notices)
    assert result.exit_code == 0


def test_kill_uses_pid_file(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    write_heartbeat(config, NOW)
    run_dir = config.paths.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "daemon.pid").write_text("4242", encoding="utf-8")
    store.add_spend(
        mission="demo",
        strategy_id=None,
        at="2026-10-04T11:30:00+00:00",
        tokens_in=0,
        tokens_out=0,
        money=Decimal("99.00"),
        source="llm",
    )
    killed = []
    monkeypatch.setattr(supervisor, "_kill", lambda pid: killed.append(pid))
    supervisor.run_once(config, notifiers=[FakeNotifier()])
    assert killed == [4242]
