from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scavenger import config as config_module
from scavenger.goal import GoalError, parse, validate

DEMO = Path(__file__).parents[2] / "docs" / "examples" / "demo-mission" / "GOAL.md"
NOW = datetime(2026, 10, 4, tzinfo=UTC)


def load_config():
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    return config_module.load(example)


def valid_goal(tmp_path):
    target = tmp_path / "demo-mission"
    target.mkdir()
    goal_file = target / "GOAL.md"
    goal_file.write_text(DEMO.read_text(encoding="utf-8"), encoding="utf-8")
    return parse(goal_file)


def codes(goal, config=None):
    return [error.code for error in validate(goal, config or load_config(), NOW)]


def test_valid_example_has_no_errors(tmp_path):
    assert codes(valid_goal(tmp_path)) == []


def test_missing_verifier_detected(tmp_path):
    goal = valid_goal(tmp_path)
    broken = replace(goal.settle, adapter="nope")
    assert "missing_verifier" in codes(replace(goal, settle=broken))


def test_missing_budget_detected(tmp_path):
    goal = valid_goal(tmp_path)
    broken = replace(goal.budget, money=Decimal(0))
    assert "missing_budget" in codes(replace(goal, budget=broken))


def test_missing_deadline_detected(tmp_path):
    goal = valid_goal(tmp_path)
    broken = replace(goal.target, deadline=datetime(2020, 1, 1, tzinfo=UTC))
    assert "missing_deadline" in codes(replace(goal, target=broken))


def test_missing_target_detected(tmp_path):
    goal = valid_goal(tmp_path)
    broken = replace(goal.target, currency="usd")
    assert "missing_target" in codes(replace(goal, target=broken))


def test_bad_ladder_detected(tmp_path):
    goal = valid_goal(tmp_path)
    assert "bad_ladder" in codes(replace(goal, ladder=goal.ladder[:2]))


def test_bad_name_detected(tmp_path):
    goal = valid_goal(tmp_path)
    assert "bad_name" in codes(replace(goal, name="BAD NAME!"))


def test_unmeasurable_statement_detected(tmp_path):
    goal = valid_goal(tmp_path)
    assert "unmeasurable_statement" in codes(
        replace(goal, statement="I want to be rich someday")
    )


def test_render_roundtrip(tmp_path):
    from scavenger.goal import render

    goal = valid_goal(tmp_path)
    target = tmp_path / "roundtrip" / "demo-mission"
    target.mkdir(parents=True)
    goal_file = target / "GOAL.md"
    goal_file.write_text(render(goal), encoding="utf-8")
    assert parse(goal_file) == goal


def test_append_assumption_preserves_body(tmp_path):
    from scavenger.goal import append_assumption

    goal = valid_goal(tmp_path)
    before = (tmp_path / "demo-mission" / "GOAL.md").read_text(encoding="utf-8")
    body_before = before.split("+++", 2)[2]
    append_assumption(tmp_path / "demo-mission" / "GOAL.md", "Extra note.", "human")
    after = (tmp_path / "demo-mission" / "GOAL.md").read_text(encoding="utf-8")
    assert after.split("+++", 2)[2] == body_before
    assert len(parse(tmp_path / "demo-mission" / "GOAL.md").assumptions) == (
        len(goal.assumptions) + 1
    )


def test_front_matter_without_closing_delimiter_is_error(tmp_path):
    from scavenger.goal import GoalParseError

    bad = tmp_path / "GOAL.md"
    bad.write_text('+++\nname = "x"\nno closing here\n', encoding="utf-8")
    try:
        parse(bad)
    except GoalParseError:
        return
    raise AssertionError("expected GoalParseError")


def test_error_codes_are_goal_errors(tmp_path):
    for error in validate(valid_goal(tmp_path), load_config(), NOW):
        assert isinstance(error, GoalError)
