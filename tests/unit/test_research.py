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
from scavenger.llm import LLMResult
from scavenger.outbox import create_outbox
from scavenger.research import Research
from scavenger.store import Store
from scavenger.switcher import Candidate

from tests.conftest import FakeLLM

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class FakeClock:
    def now(self):
        return NOW


class FakeChannel:
    def __init__(self, name, candidates):
        self.name = name
        self._candidates = list(candidates)
        self.discover_calls = 0

    def liveness(self):
        raise NotImplementedError

    def discover(self, goal):
        self.discover_calls += 1
        return list(self._candidates)

    def next_action(self, strategy, last_check):
        raise NotImplementedError


def make_candidate(
    path_key="chan:thing", channel="chan", guarantor="escrow", probability=0.5
):
    return Candidate(
        channel=channel,
        path=f"path {path_key}",
        path_key=path_key,
        outward_key="out:thing",
        payout_amount=Decimal("100.00"),
        payout_currency="USD",
        guarantor=guarantor,
        guarantor_evidence="https://grants.example/1",
        capital_needed=Decimal(0),
        hours_first_proof=1.0,
        hours_settlement=24.0,
        probability=probability,
        probability_note="channel estimate",
        rungs=({"rung": "submitted", "adapter": "attested", "locator": {}},),
    )


def make_goal():
    return Goal(
        name="demo",
        created=None,
        statement="rent of 1500 USD due",
        target=Target(
            amount=Decimal("1500.00"),
            currency="USD",
            deadline=datetime(2026, 12, 31, tzinfo=UTC),
        ),
        budget=BudgetLimit(money=Decimal("50.00"), tokens=2000000, rounds=200),
        settle=None,
        ladder=(
            LadderRung(rung="submitted", expected_wait_hours=2),
            LadderRung(rung="replied", expected_wait_hours=72),
            LadderRung(rung="settled", expected_wait_hours=336),
        ),
        strategy_defaults=StrategyDefaults(
            max_rounds=3, max_loss=Decimal("10.00"), max_pending_hours=168
        ),
    )


def setup(tmp_path, monkeypatch):
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    conf_path = tmp_path / "scavenger.toml"
    conf_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(conf_path)
    monkeypatch.setenv(config.outbox.secret_env, "x" * 32)
    store = Store.open(tmp_path / "ledger.sqlite3", run_dir=tmp_path / "run")
    store.create_mission(
        name="demo",
        goal_path="missions/demo/GOAL.md",
        goal_sha256="abc",
        status="active",
        target_amount=Decimal("1500.00"),
        target_currency="USD",
        deadline="2026-12-31T00:00:00+00:00",
        created_at="2026-10-03T05:00:00+00:00",
    )
    return config, store


def make_research(store, config, channels, llm):
    outbox = create_outbox(store, config, [], FakeClock())
    return Research(store, config, channels, llm, outbox, FakeClock())


def test_blocked_channel_skipped(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    store.block_channel("chan", "round_limit", "2026-12-31T00:00:00+00:00")
    channel = FakeChannel("chan", [make_candidate()])
    research = make_research(store, config, {"chan": channel}, FakeLLM([]))
    research.refill("demo", make_goal())
    assert channel.discover_calls == 0
    assert store.list_strategies("demo") == []


def test_dedupe_against_history(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    channel = FakeChannel("chan", [make_candidate("chan:same")])
    research = make_research(store, config, {"chan": channel}, FakeLLM([]))
    llm = FakeLLM(
        [LLMResult(text='{"probabilities": []}', tokens_in=10, tokens_out=10)]
    )
    research._llm = llm
    research.refill("demo", make_goal())
    assert len(store.list_strategies("demo")) == 1
    channel2 = FakeChannel("chan", [make_candidate("chan:same")])
    research2 = make_research(store, config, {"chan": channel2}, llm)
    research2.refill("demo", make_goal())
    assert len(store.list_strategies("demo")) == 1


def test_llm_only_fills_probability(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    candidate = make_candidate()
    channel = FakeChannel("chan", [candidate])
    llm = FakeLLM(
        [
            LLMResult(
                text='{"probabilities": [{"path_key": "chan:thing",'
                ' "probability": 0.7, "note": "hot lead"}]}',
                tokens_in=10,
                tokens_out=10,
            )
        ]
    )
    research = make_research(store, config, {"chan": channel}, llm)
    research.refill("demo", make_goal())
    stored = store.list_strategies("demo")[0]
    assert stored.probability == 0.7
    assert stored.payout_amount == Decimal("100.00")
    assert stored.path == "path chan:thing"


def test_guarantor_none_needs_approval(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    channel = FakeChannel("chan", [make_candidate("chan:bare", guarantor="none")])
    llm = FakeLLM(
        [LLMResult(text='{"probabilities": []}', tokens_in=10, tokens_out=10)]
    )
    research = make_research(store, config, {"chan": channel}, llm)
    research.refill("demo", make_goal())
    stored = store.list_strategies("demo")[0]
    assert stored.status == "awaiting_approval"
    drafts = store.list_outbox("awaiting")
    assert len(drafts) == 1
    assert drafts[0].kind == "strategy_start"


def test_empty_refill_counter(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    channel = FakeChannel("chan", [])
    research = make_research(store, config, {"chan": channel}, FakeLLM([]))
    research.refill("demo", make_goal())
    assert store.get_mission("demo").empty_refills == 1
    channel._candidates = [make_candidate()]
    llm = FakeLLM(
        [LLMResult(text='{"probabilities": []}', tokens_in=10, tokens_out=10)]
    )
    research._llm = llm
    research.refill("demo", make_goal())
    assert store.get_mission("demo").empty_refills == 0


def test_token_cap_stops_run(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    candidates = [make_candidate(f"chan:{index}") for index in range(25)]
    channel = FakeChannel("chan", candidates)
    llm = FakeLLM(
        [
            LLMResult(
                text='{"probabilities": []}', tokens_in=200000, tokens_out=200000
            ),
        ]
    )
    research = make_research(store, config, {"chan": channel}, llm)
    research.refill("demo", make_goal())
    assert len(llm.calls) == 1
    assert len(store.list_strategies("demo")) <= 20


def test_channel_block_after_two_same_cause_deaths(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    for index in range(2):
        strategy_id = store.add_strategy(
            mission="demo",
            channel="chan",
            path=f"old {index}",
            path_key=f"chan:old-{index}",
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
            status="dead",
            death_cause="round_limit",
            rung_reached="submitted",
            failed_rounds=3,
            money_spent=Decimal(0),
            tokens=0,
            hours_to_rung_json="{}",
            closed_at="2026-10-04T11:00:00+00:00",
        )
    channel = FakeChannel("chan", [make_candidate("chan:new")])
    research = make_research(store, config, {"chan": channel}, FakeLLM([]))
    research.refill("demo", make_goal())
    assert channel.discover_calls == 0
    assert store.active_block("chan") is not None
