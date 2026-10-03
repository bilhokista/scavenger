import re
from datetime import UTC, datetime
from pathlib import Path

from scavenger.config import load as load_config
from scavenger.goal import parse as parse_goal
from scavenger.goal import validate

SKILL = Path(__file__).parents[2] / "SKILL.md"


def test_skill_template_parses_and_validates(tmp_path):
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: scavenger" in text.split("---")[1]
    match = re.search(r"```markdown\n(\+\+\+\n.*?)```", text, re.DOTALL)
    assert match, "SKILL.md must contain a GOAL.md template block"
    target = tmp_path / "demo-mission"
    target.mkdir()
    (target / "GOAL.md").write_text(match.group(1), encoding="utf-8")
    goal = parse_goal(target / "GOAL.md")
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    config = load_config(example)
    now = datetime(2026, 10, 4, tzinfo=UTC)
    assert validate(goal, config, now) == []
