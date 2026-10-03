#!/usr/bin/env python3
"""SessionStart: remind the agent of goal-loop discipline."""

import json
import sys

REMINDER = (
    "Scavenger discipline: check available skills before acting. "
    "If the user gives a vague money brief, compile missions/<name>/GOAL.md "
    "first (verifier, budget, deadline are never assumed) and only then "
    "research. No outcome claim without evidence."
)


def main() -> int:
    try:
        sys.stdin.read()
    except OSError:
        pass
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": REMINDER,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
