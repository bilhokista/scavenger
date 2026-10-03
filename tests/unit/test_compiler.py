import json
from datetime import UTC, datetime
from pathlib import Path

from scavenger.config import load as load_config
from scavenger.llm import LLMResult
from scavenger.store import Store

from tests.conftest import FakeLLM

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

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


class FakeClock:
    def now(self):
        return NOW


def setup(tmp_path, monkeypatch):
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    conf_path = tmp_path / "scavenger.toml"
    conf_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(conf_path)
    monkeypatch.setenv(config.outbox.secret_env, "x" * 32)
    store = Store.open(tmp_path / "ledger.sqlite3", run_dir=tmp_path / "run")
    return config, store


def draft_result(draft):
    return LLMResult(text=json.dumps(draft), tokens_in=10, tokens_out=10)


def compile_here(store, config, llm, brief, answers):
    from scavenger.compiler import compile_brief

    asked = []

    def ask(question):
        asked.append(question)
        return answers.pop(0) if answers else None

    goal = compile_brief(brief, ask, config, llm=llm, store=store, clock=FakeClock())
    return goal, asked


def test_never_assumed_field_not_filled_by_silence(tmp_path, monkeypatch):
    from scavenger.compiler import MissingRequiredField

    config, store = setup(tmp_path, monkeypatch)
    draft = dict(VALID_DRAFT)
    draft.pop("settle")
    llm = FakeLLM([draft_result(draft), draft_result(draft)])
    try:
        compile_here(store, config, llm, "cari duit", [None])
    except MissingRequiredField as error:
        assert "verifier" in error.field
        return
    raise AssertionError("expected MissingRequiredField")


def test_at_most_three_questions(tmp_path, monkeypatch):
    from scavenger.compiler import MissingRequiredField

    config, store = setup(tmp_path, monkeypatch)
    broken = {
        "name": "BAD NAME!",
        "statement": "get rich someday",
        "target": {"amount": "0", "currency": "usd", "deadline": None},
        "budget": {"money": "0", "tokens": 0, "rounds": 0},
        "ladder": [{"rung": "only", "expected_wait_hours": 1}],
    }
    llm = FakeLLM([draft_result(broken)] * 5)
    try:
        compile_here(store, config, llm, "cari duit", ["a1", "a2", "a3", "a4"])
    except MissingRequiredField:
        pass
    assert len(llm.calls) <= 4  # 1 draft + at most 3 patches


def test_silence_logs_assumption(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    bad = dict(VALID_DRAFT, statement="get rich someday")
    fixed = dict(VALID_DRAFT)
    llm = FakeLLM([draft_result(bad), draft_result(fixed)])
    goal, asked = compile_here(store, config, llm, "cari duit", [None])
    assert len(asked) == 1
    assert any(assumption.source == "silence" for assumption in goal.assumptions)
    assert (tmp_path / "missions" / "demo" / "GOAL.md").exists()


def test_missing_required_after_three_raises(tmp_path, monkeypatch):
    from scavenger.compiler import MissingRequiredField

    config, store = setup(tmp_path, monkeypatch)
    broken = dict(VALID_DRAFT)
    broken.pop("settle")
    broken.pop("budget")
    broken["target"] = dict(VALID_DRAFT["target"], deadline=None)
    llm = FakeLLM([draft_result(broken)] * 5)
    try:
        compile_here(store, config, llm, "cari duit", [None, None, None])
    except MissingRequiredField:
        return
    raise AssertionError("expected MissingRequiredField")


def test_string_shaped_draft_becomes_questions_not_crash(tmp_path, monkeypatch):
    from scavenger.compiler import MissingRequiredField

    config, store = setup(tmp_path, monkeypatch)
    sloppy = {
        "name": "demo",
        "statement": "cari duit 200 USD",
        "target": "200 USD",
        "budget": "50 USD",
        "settle": "bank email",
        "ladder": ["submitted", "settled"],
    }
    llm = FakeLLM([draft_result(sloppy)] * 5)
    try:
        compile_here(store, config, llm, "cari duit", [None] * 3)
    except MissingRequiredField:
        return
    raise AssertionError("expected MissingRequiredField")


def test_goal_written_before_any_other_mission_file(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    llm = FakeLLM([draft_result(VALID_DRAFT)])
    goal, asked = compile_here(store, config, llm, "cari duit", [])
    assert asked == []
    mission_dir = tmp_path / "missions" / "demo"
    assert (mission_dir / "GOAL.md").exists()
    assert not (mission_dir / "outbox").exists()
    assert not (mission_dir / "work").exists()
    assert store.get_mission("demo") is not None
    assert goal.name == "demo"
