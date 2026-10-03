# Authoring channels

A channel turns the outside world into ranked candidates and turns
strategy rounds into actions.

## Protocol (`daemon/scavenger/channels/__init__.py`)

- `liveness() -> Liveness`: cheap probe. Dead means skipped with a
  warn event, never fatal.
- `discover(goal) -> list[Candidate]`: every candidate cites its
  source in `source_urls`. No cold outreach: a candidate without a
  cited message id or posting URL is never created.
- `next_action(strategy, last_check) -> Action`: `draft` carries a
  full `DraftSpec`, `local` means code work for the agent runner,
  `wait` means do nothing this round.

## Minimal example

```python
from scavenger.channels import Action

class MyChannel:
    name = "mine"

    def discover(self, goal):
        return []  # real candidates with cited sources

    def next_action(self, strategy, last_check):
        return Action(
            kind="wait", draft=None, description="waiting",
            fingerprint=f"mine:{strategy.path_key}",
        )
```

Register with `channels.register` or pass the instance in the
`channels` dict. Add the name to `[channels] enabled` handling in
`cli.build_stack`.

## Required tests

- Discovery builds candidates with cited sources; uncited items are
  skipped.
- Registry round-trip (`test_registry` pattern).
- One test per `next_action` branch.
- Fixture-driven: no network in tests, ever.
