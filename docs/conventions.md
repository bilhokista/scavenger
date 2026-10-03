# Conventions

Contributor rules for this repo. Normative details live in the implementation
plan; on conflict the plan wins and this file is updated in the same commit.

## Working rules

1. Test first, every task. Write the tests, watch them fail, implement,
   watch them pass. A task is done only when its acceptance checks pass.
2. No network in tests. HTTP goes through `httpx` with `respx` or recorded
   fixtures. IMAP/SMTP sit behind small client classes replaced by fakes
   in tests. A test opening a socket is a bug.
3. No invented platforms. No bounty board, grant program, or job feed is
   named, hardcoded, or supported until it is checked live (reachable,
   active, paying) and recorded with date and evidence URLs in
   `docs/channels/LIVENESS.md`.
4. No outward action outside `senders/`. Nothing elsewhere sends email,
   opens PRs, posts, submits forms, or moves money. A test enforces this.
5. Every external call has a timeout (default 20 s) and failures surface
   as typed errors, never silent `None` or empty lists. Unreachable
   sources report `unverified`.
6. Money is `decimal.Decimal`, stored in sqlite as TEXT. Never `float` for
   money. Timestamps are timezone-aware UTC, ISO 8601 TEXT.
7. Secrets come from environment variables only. Config holds variable
   names, never values. Startup fails loudly on a missing variable.
8. One commit per task, Conventional Commits, plain-prose body saying what
   and why. No AI co-author trailers. Before every commit: `ruff check`,
   `ruff format --check`, `pytest`, `git diff --check`.
9. Comments explain why where it is not obvious, never restate the code.
10. Ambiguity the plan does not cover goes to
    `docs/superpowers/plans/QUESTIONS.md`, not into guesses. Continue with
    tasks that do not depend on it.

## Reference

### Strategy state machine

```
queued --(guarantor none)--> awaiting_approval --approve--> queued
                                               --reject---> dead(human_rejected)
queued --switcher picks--> active
active --round sent, check pending--> pending
active --check pass, not last rung--> active (rung_index += 1)
active/pending --check pass on last rung--> won
active/pending --check fail--> failed_rounds += 1;
     failed_rounds >= max_rounds --> dead(round_limit) else active
pending --pending_since + max_pending_hours < now--> fail (as above, cause pending_timeout if it kills)
any --3rd consecutive unverified--> needs_human (escalation notice)
needs_human --`scavenger resume <id>`--> previous status
any non-terminal --loss >= max_loss--> dead(loss_limit)
any non-terminal --channel liveness fails--> dead(channel_dead)
any non-terminal --mission stops--> dead(mission_stopped)
```

`won` and `dead` are terminal and write one `ledger` row each in the same
transaction as the status change. `max_rounds` counts failed rounds only;
`pass` and `pending` do not consume it. Mission `budget.rounds` counts
failed rounds across all strategies. Pending timeout comes from the
current rung's `max_pending_hours`, else `strategy_defaults`.

### Keys and fingerprints

- `path_key`: `"{channel}:{canonical_url_or_id}"`, lowercase, no query
  string, no trailing slash. Same `path_key` in one mission means same
  strategy; research dedupes on it.
- `outward_key`: the account or resource written to, e.g.
  `github:owner/repo`, `email:alice@example.com`, `form:host`.
- `action_fingerprint`: sha256 hex of `kind + "\n" + target + "\n" + body`
  with whitespace collapsed and lowercased.
- `content_sha256` for outbox: sha256 hex of canonical JSON of
  `{kind, target, body, links, payload, cost}` with sorted keys.

### Approval token

```
message = f"{outbox_id}|{content_sha256}|{expires_at_iso}".encode()
token   = base64.b32encode(hmac.new(secret, message, "sha256").digest())[:20].decode()
```

Verification recomputes from the current row; an edited body changes
`content_sha256` and the old token fails. Compare with
`hmac.compare_digest`. Tokens are never stored.

### Ranking formula (`switcher.score`)

```
gap              = target - settled_in_target
payout_target    = fx(payout_amount, payout_currency -> target_currency)  # None -> drop
eta              = now + hours_settlement
if eta > deadline:               drop ("cannot settle before deadline")
if payout_target < gap * gap_floor_ratio:  drop ("below gap floor")
expected         = payout_target * probability
est_cost         = capital_needed (converted to budget currency)
                   + estimated tokens * blended token price
score            = expected / max(est_cost, Decimal("0.01"))
```

Sort: guarantor `none` last; then score descending; then
`hours_first_proof` ascending; then `capital_needed` ascending; then
`path_key` ascending (deterministic).

Probability: with at least `ledger.min_history_for_probability` closed
rows for the channel, `p = (won + 1) / (n + 2)`, source `ledger`.
Otherwise the research estimate clamped to `[0.01, 0.90]`, source
`estimate`, with the one-sentence reason in `probability_note`.

### Channel blocking

When a strategy dies, count ledger rows for the same `channel` and
`death_cause` within `block_days`. On reaching
`block_after_same_cause_deaths`, upsert `channel_blocks` with
`until = now + block_days`. Research skips blocked channels.
`scavenger unblock <channel>` deletes the row. A passing liveness check
alone does not unblock. Defaults: 2 deaths, 14 days, manual unblock.
