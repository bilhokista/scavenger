# Contributing

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate  # Windows; source .venv/bin/activate elsewhere
pip install -e ".[dev]"
```

## Test commands

```bash
pytest
pytest --cov=scavenger --cov-fail-under=80
ruff check .
ruff format --check .
git diff --check
```

Run all five before every commit. One commit per task, Conventional
Commits, plain-prose body, no AI co-author trailers.

## Conventions

Read `docs/conventions.md` first. The short version: test first, no
network in tests, no invented platforms, no outward action outside
`senders/`, money is `Decimal`, secrets from env only, ambiguity goes
to `docs/superpowers/plans/QUESTIONS.md` instead of guesses.

## Adding a channel, adapter, notifier, or sender

- Channel: `docs/authoring-channels.md`
- Adapter: `docs/authoring-adapters.md`
- Notifier: `docs/authoring-notifiers.md`
- Sender: mirror `daemon/scavenger/senders/manual.py`, register the
  kind in your call sites, add fixture-driven tests. Unit tests must
  never push, post, or send; the GitHub PR sender test shows the
  local-bare-repo pattern.
