import json
from decimal import Decimal
from pathlib import Path

from freezegun import freeze_time
from scavenger import clock
from scavenger.adapters import ProofResult, ProofState, Settlement
from scavenger.channels import Action
from scavenger.config import load as load_config
from scavenger.executor import Executor
from scavenger.loop import Loop
from scavenger.outbox import DraftSpec, create_outbox
from scavenger.senders import SendResult
from scavenger.store import Store
from scavenger.verifier import Verifier

GOAL_TEXT = """+++
name = "demo"
statement = "rent of 1500 USD due"

[target]
amount = "1500.00"
currency = "USD"
deadline = 2026-12-31T00:00:00Z

[budget]
money = "50.00"
tokens = 20000000
rounds = 200

[[ladder]]
rung = "submitted"
expected_wait_hours = 2
[[ladder]]
rung = "replied"
expected_wait_hours = 72
[[ladder]]
rung = "settled"
expected_wait_hours = 336

[strategy_defaults]
max_rounds = 3
max_loss = "10.00"
+++

Body.
"""

FROZEN_START = "2026-10-04 12:00:00"


def passing(settlement=None):
    return ProofResult(
        state=ProofState.PASS,
        evidence_raw="ok",
        detail="ok",
        settlement=settlement,
    )


def pending():
    return ProofResult(state=ProofState.PENDING, evidence_raw="", detail="wait")


def failing():
    return ProofResult(state=ProofState.FAIL, evidence_raw="bad", detail="bad")


class FakeAdapter:
    def __init__(self, name, results):
        self.name = name
        self._results = list(results)

    def check(self, rung, locator, since):
        return self._results.pop(0)


class FakeChannel:
    name = "chan"

    def liveness(self):
        from types import SimpleNamespace

        return SimpleNamespace(alive=True)

    def discover(self, goal):
        return []

    def next_action(self, strategy, last_check):
        return Action(
            kind="draft",
            draft=DraftSpec(kind="email", target="boss@example.com", body="hello"),
            description="send intro",
            fingerprint=f"fp-{strategy.id}",
        )


class FakeSender:
    kind = "email"
    counter = 0

    def send(self, item):
        FakeSender.counter += 1
        return SendResult(
            ok=True,
            locator={"message": f"m-{FakeSender.counter}"},
            detail="sent",
        )


class FakeResearch:
    def __init__(self, store, batches):
        self._store = store
        self._batches = list(batches)

    def refill(self, mission):
        from scavenger import clock as real_clock

        specs = self._batches.pop(0) if self._batches else []
        if not specs:
            self._store.note_empty_refill(mission)
        for spec in specs:
            self._store.add_strategy(
                mission=mission,
                channel="chan",
                path=spec["path"],
                path_key=f"chan:{spec['path']}",
                outward_key="out:x",
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
                rungs_json=json.dumps(spec["rungs"]),
                created_at=real_clock.now().isoformat(),
            )
        self._store.note_research(mission, real_clock.now().isoformat())


def rungs(name, adapter, max_rounds=3, max_pending=None):
    return [
        {
            "rung": name,
            "adapter": adapter,
            "locator": {},
            "max_rounds": max_rounds,
            "max_pending_hours": max_pending,
        }
    ]


def setup_world(tmp_path, monkeypatch, batches, adapters):
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
        deadline="2026-12-31T00:00:00+00:00",
        created_at="2026-10-04T12:00:00+00:00",
    )
    outbox = create_outbox(store, config, [], clock)
    verifier = Verifier(store, config, adapters, clock)
    channel = FakeChannel()
    executor = Executor(store, config, {"chan": channel}, outbox, clock)
    research = FakeResearch(store, batches)
    loop = Loop(
        store,
        config,
        verifier,
        executor,
        outbox,
        {"chan": channel},
        {"email": FakeSender()},
        [],
        research,
        clock,
    )
    return config, store, outbox, loop


def approve_all(store, outbox):
    for item in store.list_outbox("awaiting"):
        outbox.approve(item.id, None, via="cli", by="human")


def reject_all(store, outbox):
    for item in store.list_outbox("awaiting"):
        outbox.reject(item.id, via="cli", by="human")


def strategies_by_path(store):
    return {strategy.path: strategy for strategy in store.list_strategies("demo")}


def test_full_mission(tmp_path, monkeypatch):
    submitted_a = rungs("submitted", "adapter-a", max_rounds=3, max_pending=1)
    submitted_b = rungs("submitted", "adapter-b", max_rounds=1)
    climbed_c = [
        {"rung": "submitted", "adapter": "adapter-c", "locator": {}, "max_rounds": 5},
        {"rung": "settled", "adapter": "adapter-c", "locator": {}, "max_rounds": 5},
    ]
    settled_d = rungs("settled", "adapter-d", max_rounds=5)
    batches = [
        [{"path": "A", "rungs": submitted_a}, {"path": "B", "rungs": submitted_b}],
        [{"path": "C", "rungs": climbed_c}],
        [{"path": "D", "rungs": settled_d}],
    ]
    settle_900 = Settlement(amount=Decimal("900.00"), currency="USD", message_id="m-c1")
    settle_600 = Settlement(amount=Decimal("600.00"), currency="USD", message_id="m-d1")
    adapters = {
        "adapter-a": FakeAdapter("adapter-a", [pending()] * 8),
        "adapter-b": FakeAdapter("adapter-b", [failing()]),
        "adapter-c": FakeAdapter("adapter-c", [passing(), passing(settle_900)]),
        "adapter-d": FakeAdapter("adapter-d", [passing(settle_600)]),
    }
    _config, store, outbox, loop = setup_world(tmp_path, monkeypatch, batches, adapters)
    with freeze_time(FROZEN_START) as frozen:
        loop.tick()  # refill 1, A drafted
        assert strategies_by_path(store)["A"].status == "active"
        approve_all(store, outbox)
        loop.tick()  # A sent + pending, B drafted
        assert strategies_by_path(store)["A"].status == "pending"
        reject_all(store, outbox)
        loop.tick()  # B round fails, B dead; A pending again
        assert strategies_by_path(store)["B"].status == "dead"
        frozen.move_to("2026-10-04 14:05:00")
        loop.tick()  # A timeout fail 1
        loop.tick()  # A timeout fail 2
        loop.tick()  # A timeout fail 3 -> dead
        assert strategies_by_path(store)["A"].status == "dead"
        assert strategies_by_path(store)["A"].death_cause == ("pending_timeout")
        loop.tick()  # refill 2, C drafted
        approve_all(store, outbox)
        loop.tick()  # C sent, climbs to settled, drafts again
        approve_all(store, outbox)
        loop.tick()  # C sent, settles 900, won
        assert strategies_by_path(store)["C"].status == "won"
        frozen.move_to("2026-10-04 16:10:00")
        loop.tick()  # refill 3, D drafted
        approve_all(store, outbox)
        loop.tick()  # D sent, settles 600, won
        loop.tick()  # mission done
        assert store.get_mission("demo").status == "done"
    report = (tmp_path / "missions" / "demo" / "REPORT.md").read_text(encoding="utf-8")
    assert "1500.00 / 1500.00 USD" in report
    assert "pending_timeout" in report
    assert "round_limit" in report
    assert "evidence" in report


def test_budget_stop_marks_open_strategy(tmp_path, monkeypatch):
    batches = [[{"path": "A", "rungs": rungs("submitted", "adapter-a")}]]
    adapters = {"adapter-a": FakeAdapter("adapter-a", [pending()] * 4)}
    _config, store, _outbox, loop = setup_world(
        tmp_path, monkeypatch, batches, adapters
    )
    with freeze_time(FROZEN_START):
        loop.tick()  # refill, A drafted
        store.add_spend(
            mission="demo",
            strategy_id=None,
            at="2026-10-04T12:30:00+00:00",
            tokens_in=0,
            tokens_out=0,
            money=Decimal("99.00"),
            source="capital",
        )
        loop.tick()  # budget stop
        mission = store.get_mission("demo")
        assert mission.status == "stopped_budget"
        strategies = store.list_strategies("demo")
        assert strategies[0].status == "dead"
        assert strategies[0].death_cause == "mission_stopped"
        assert (tmp_path / "missions" / "demo" / "REPORT.md").exists()


def test_two_empty_refills_is_impossible(tmp_path, monkeypatch):
    _config, store, _outbox, loop = setup_world(tmp_path, monkeypatch, [[], []], {})
    with freeze_time(FROZEN_START) as frozen:
        loop.tick()  # empty refill 1
        frozen.move_to("2026-10-04 14:05:00")
        loop.tick()  # empty refill 2
        loop.tick()  # nothing live, two empties -> impossible
        assert store.get_mission("demo").status == "impossible"
