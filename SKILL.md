---
name: scavenger
description: Compile a vague money brief into a verified GOAL.md contract before any work starts.
---

# Scavenger brief compiler

Turn a bossy, vague brief ("cari duit", "find clients") into a
machine-checkable goal contract. No research, no outreach, no code
until `GOAL.md` exists and validates.

Works with any agent that loads skills. If the `scavenger` CLI is
installed, finish with `scavenger validate <mission>`; otherwise check
the validation table by hand.

## Procedure

1. Enter plan mode on receiving the brief. Do not act on it directly.
2. Draft the goal: who is blocked from what, by what structural
   obstacle, losing how much, with what workaround failing. The loss
   must carry a number.
3. Fill the template below. The three never-assumed fields
   (verifier, budget, deadline) must come from the human or from
   hard evidence, never from silence.
4. Ask at most 3 questions, never-assumed fields first, one per
   validation error. Silence on any other field means keeping your
   value and logging an `[[assumptions]]` entry with
   `source = "silence"`.
5. After 3 questions, any still-failing never-assumed field stops the
   mission brief: report exactly which field and which question would
   resolve it. Do not invent the answer.
6. Write `missions/<name>/GOAL.md` and only then proceed to research.

## Validation table

| Code | Fails when |
|---|---|
| `missing_verifier` | `[settle]` absent, adapter unknown, or rule name unconfigured |
| `missing_budget` | `[budget]` absent, or money/tokens/rounds missing or <= 0 |
| `missing_deadline` | `target.deadline` absent or not in the future |
| `missing_target` | amount <= 0 or currency not 3 uppercase letters |
| `bad_ladder` | fewer than 3 or more than 5 rungs, duplicates, last rung not `settled` |
| `bad_name` | name pattern fails or differs from directory name |
| `unmeasurable_statement` | statement empty or contains no number |

## GOAL.md template

Fill the bracketed values. Dates must stay in the future.

```markdown
+++
name = "demo-mission"
statement = "Freelance developer has no income this month; rent of 1500 USD is due 2030-11-30."

[target]
amount = "1500.00"
currency = "USD"
deadline = 2030-11-30T16:59:59Z

[budget]
money = "50.00"
tokens = 2000000
rounds = 200

[settle]
adapter = "payment_email"
rules = ["example-bank"]

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

## Statement

Why this mission exists, in plain words.

## Assumptions log

Mirror of [[assumptions]] above, appended over time.
```
