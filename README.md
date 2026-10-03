# Scavenger

Vague brief in, verified goal out. Scavenger turns bossy one-liners
("cari duit") into machine-checkable goal contracts, then loops
strategies until the goal is proven met or proven impossible.

## What it never does

- Sends anything without approval. Every outward action waits in the
  outbox for a human-signed token or a local CLI approval.
- Guarantees income. Ranking is expected value, not a promise.
- Spends past budget. Strategy, mission, and emergency brakes all hold.

## Install

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
scavenger --version
```

## Quickstart (no credentials)

```bash
scavenger init --config ./scavenger.toml
scavenger run --fake --config ./scavenger.toml
```

The fake run completes a demo mission in under a minute with scripted
channels and adapters. See `docs/conventions.md` for contributor rules
and `docs/supervisor.md` for running the watchdog.

## Configuration

Full reference with every default lives in `scavenger.example.toml`.
Secrets come from environment variables only; the config holds variable
names. Key sections: `paths`, `llm`, `budget`, `fx`, `loop`,
`verifier`, `research`, `ledger`, `outbox`, `supervisor`, `executor`,
`github`, `imap`, `smtp`, `payment_rules`, `notifiers`, `channels`.

## Safety model

- Approval gate: HMAC tokens, expiry, tamper-evident drafts, standing
  sending limits rechecked at send time.
- Verifier fetches its own evidence from adapter locators only; it
  never takes executor output.
- Brakes: per-strategy round and loss limits, per-mission budget and
  deadline, plus an outside supervisor that kills runaway daemons.

## Limitations

- Channels only cover what `docs/channels/LIVENESS.md` verified live.
  Everything else is manual entry or RSS until surveyed.
- LLM probability estimates are guesses clamped to 0.01–0.90; ledger
  history replaces them as evidence accumulates.
- Payout needs a human somewhere: approval, mark-sent, or maintainer
  merge. The machine never declares money on its own.
