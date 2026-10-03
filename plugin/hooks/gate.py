#!/usr/bin/env python3
"""PreToolUse gate: no work without a valid GOAL.md contract.

Reads the hook JSON from stdin. If <cwd>/.scavenger/active_mission names
a mission whose missions/<name>/GOAL.md is missing or fails the
never-assumed checks (verifier, budget, deadline), the tool call is
denied. Writes to GOAL.md itself are always allowed. Bash calls pass
through: the target cannot be determined cheaply.
"""

import json
import sys
import tomllib
from pathlib import Path


def goal_problems(goal_file: Path) -> list:
    try:
        text = goal_file.read_text(encoding="utf-8")
    except OSError:
        return ["GOAL.md missing"]
    lines = text.split("\n")
    if not lines or lines[0].strip() != "+++":
        return ["GOAL.md has no front matter"]
    try:
        close = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "+++"
        )
        front = tomllib.loads("\n".join(lines[1:close]))
    except (StopIteration, tomllib.TOMLDecodeError):
        return ["GOAL.md front matter unreadable"]
    problems = []
    settle = front.get("settle")
    if not settle or not settle.get("adapter"):
        problems.append("missing verifier: no [settle] adapter")
    budget = front.get("budget") or {}
    if not all(_positive(budget.get(key)) for key in ("money", "tokens", "rounds")):
        problems.append("missing budget: money, tokens, rounds above zero")
    target = front.get("target") or {}
    if not target.get("deadline"):
        problems.append("missing deadline: target.deadline not set")
    return problems


def _positive(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (OSError, ValueError):
        return 0
    tool = payload.get("tool_name", "")
    if tool not in ("Write", "Edit"):
        return 0
    path = str((payload.get("tool_input") or {}).get("file_path", ""))
    if path.endswith("GOAL.md"):
        return 0
    cwd = Path(payload.get("cwd", "."))
    try:
        mission = (
            (cwd / ".scavenger" / "active_mission")
            .read_text(encoding="utf-8")
            .strip()
            .splitlines()[0]
        )
    except (OSError, IndexError):
        return 0
    if not mission:
        return 0
    problems = goal_problems(cwd / "missions" / mission / "GOAL.md")
    if not problems:
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Mission '{mission}' has no valid GOAL.md: "
                        + "; ".join(problems)
                        + ". Compile it first."
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
