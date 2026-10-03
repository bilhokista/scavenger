# Scavenger — goal-loop agent system (design spec)

Date: 2026-10-03. Status: draft for review, not approved for implementation.

## 1. Intent

Humans brief vaguely and bossily ("make money"). Scavenger turns a vague
brief into a machine-checkable goal contract, then loops strategies until
the goal is proven met or proven impossible. Fail fast, fail forward:
every dead strategy leaves data for the next one.

Success is measured in settled money against the mission target, not in
rounds run, drafts written, or strategies tried. A mission that produced
activity but no settled money is a failed mission and its report says so.

Non-goals: replacing schedulers for routine fixed tasks, guaranteeing
income, spending without caps, acting outward without human approval.

## 2. Form

Three detachable layers, brain is the daemon:

- `daemon/` owns the loop, strategy queue, verifier, brakes. Runs 24/7.
- `plugin/` enforces per-response discipline inside chat sessions: plan
  gate, skill-first, evidence before claims.
- `SKILL.md` holds brief-compiler prompts, attachable to any agent.

Daemon runs without plugin. Plugin runs without daemon. Skill attaches
anywhere. No monolith. All three read and write the same `missions/`
directory and state store, so a mission compiled in chat can be picked up
by the daemon.

Integration rule: any existing poller, scraper, or prospector brought in
as a channel ships with tests that prove it fails loudly on empty or
broken input and never emits a record it did not fetch.

Name: scavenger. It sweeps, picks up what has value, moves on when the
ground is dry.

## 3. Brief Compiler (mission entry)

On receiving a brief the agent enters plan mode automatically. Output is
`missions/<name>/GOAL.md` before any other work. Contents:

- Atomic statement: who is blocked from what, by what, losing how much.
  Unmeasurable loss rejects back to the human with one sharp question.
- Target: amount, currency, and deadline. The gap (target minus settled)
  drives strategy ranking in section 4.
- Verifier definition: what evidence proves done, which adapter fetches
  it, from where (payment notification, merged PR, inbox reply). No
  machine-checkable verifier, no mission start.
- Sub-goal ladder: 3-5 rungs toward money, each with its own proof and
  its own expected wait time. Example: submitted, replied, deposit in,
  settled.
- Brakes: max budget (tokens, money, rounds), deadline, death criteria
  per strategy (round limit, loss limit, max pending time) plus required
  evidence of death.
- Bossy rule: human owes at most 3 sharpening answers. Silence means the
  agent logs assumptions in the file and proceeds. Logged assumptions can
  be corrected later.

Three fields can never be assumed: verifier, budget, deadline. If any of
them is missing after the 3 answers, the mission does not start and the
agent asks for that field only. Everything else may be assumed and
logged.

Order violation (work before GOAL.md) equals failure.

## 4. Research + Strategy Switcher

Research runs on new missions and dead strategies. Three sources:
internet (web, GitHub, docs), local data (repos, notes, past missions,
strategy ledger), executable channels (bounty boards, RSS, APIs). Every
channel is checked live before it enters the queue: reachable, recently
active, paying. Platforms known from model training data alone never
qualify.

Output is at most 5 strategies, one line each: path, payout size,
payment proof, payment guarantor (escrow, grant program, signed client),
capital needed, time to first proof, time to settlement.

Ranking: expected settled money before the deadline, per unit of budget.
Expected money is payout times probability of settlement, where
probability comes from the strategy ledger when history exists and from
an explicit, logged estimate when it does not. Strategies whose best case
cannot close a meaningful share of the gap (default floor: 5% of the
gap) are dropped. Ties break on fastest proof, then smallest capital.
Strategies without a payment guarantor rank last and need human approval
to run.

Switcher: always execute the top strategy first. A strategy dies on round
limit, loss limit, or max pending time; death is logged with evidence and
the switcher moves to the next. No drama. Fast death with records is fail
fast. Deaths that leave data (wrong contacts, a target repo with a broken
main branch, overpriced paths) are fail forward.

Strategies may run in parallel only when they share no budget pool and
no outward channel (no two strategies writing to the same repo, inbox, or
platform account).

Hard rule: no new mid-run strategies while the queue still holds a
runnable strategy, unless data shows an opportunity expiring within
hours. Anti-wandering brake. A strategy that fails or goes `pending`
stops blocking: the switcher moves to the next runnable strategy at
once. When no runnable strategy is left (every one dead or pending),
research runs a fresh deep search across all channels, skipping paths
the ledger blocked, and refills the queue. Pending strategies keep being
checked by the verifier and re-enter the loop on `pass`.

### Strategy ledger

`ledger.sqlite3` records every strategy across all missions: channel,
path, rounds, spend, rung reached, outcome, death cause, time to each
rung. Research reads it first. A channel that died twice for the same
cause is blocked until data shows the cause changed. This is how deaths
become forward data instead of a log nobody reads.

## 5. Executor + Verifier + Budget brakes

Executor loop: take active strategy, run the smallest round producing
proof (not result), hand control to verifier, log. One round, one proof,
always. Draft before send, dry-run before submit, read main before code.

### Human approval gate

Any outward or irreversible action stops for human approval: sending a
message, opening a PR or issue, submitting an application, spending
money, posting publicly. The executor prepares the action as a ready
draft (target, full text, links, cost) in `missions/<name>/outbox/` and
notifies the human. No approval, no send. Approvals are per action, never
blanket.

The gate also enforces configurable standing limits. Defaults: max 3
open PRs per repository, escrow or grant proof checked before bounty
work, no bulk outreach.

### Verifier

Verifier is blind to who executed and never reads proof from the
executor. It fetches evidence itself through adapters that query the
source: GitHub API for PR state and merge, inbox API for replies,
payment notification email for incoming money, platform API for payout
status. The executor only tells the verifier where to look (PR URL,
thread id, invoice number). If the adapter cannot reach the source, the
result is `unverified`, never `pass`.

Adapters are deterministic code. An LLM may be used only to classify
fetched content (for example: is this reply a yes), and its input is the
raw fetched content, not an executor summary.

Each proof check returns one of four states:

- `pass`: evidence found, strategy climbs the ladder.
- `pending`: action was taken and the source shows it is waiting on an
  outside party (PR open, email sent, invoice issued). Pending does not
  count as a failed round. It has a max pending time from GOAL.md; when
  exceeded it becomes `fail`.
- `fail`: evidence shows rejection or absence past its window. Counts
  toward the strategy limit.
- `unverified`: source unreachable. Retried with backoff; three in a row
  escalates to the human.

While a strategy is pending, the executor may work the next strategy in
the queue if section 4 parallel rules allow it.

### Brakes

Three brake layers. Strategy: round, loss, and max pending limits.
Mission: total budget and deadline from GOAL.md, hit either and the
mission stops with a report. Emergency: an outside supervisor kills on
anomalous hourly spend or spinning in place (same action and same
non-pending result three rounds straight).

The supervisor is a separate process started by the OS scheduler (cron,
systemd timer, or Windows Task Scheduler), reading the state store and
spend logs on its own schedule. It shares no code path with the loop
beyond the store schema, so a hung or looping daemon cannot silence it.
Emergency stops always carry the last report and a notification, never
vanish silently.

### Mission report

Every stop (done, impossible, budget, deadline, emergency) writes
`missions/<name>/REPORT.md`: settled amount versus target, rung reached
per strategy, spend, deaths with evidence, and what the ledger learned.

## 6. Repo shape

```
scavenger/
  SKILL.md
  plugin/
  daemon/
    scavenger/          # importable package; see note below
      loop.py
      research.py
      switcher.py
      executor.py
      verifier.py
      channels/
      adapters/
        github.py
        inbox.py
        payment_email.py
      senders/
      notifiers/
      brake.py
      supervisor.py
      store.py
  missions/<name>/
    GOAL.md
    outbox/
    REPORT.md
  tests/
  docs/conventions.md
```

The modules live in `daemon/scavenger/` rather than directly in
`daemon/`, because a top-level `daemon` import name collides with the
existing `python-daemon` package. `senders/` performs approved outward
actions; nothing else in the daemon talks outward.

`store.py` owns the sqlite schema for missions, rounds, proof checks, and
the strategy ledger. Every module reads and writes state through it.

`missions/` and `ledger.sqlite3` hold user data. Both are gitignored;
the repo ships an example mission under `docs/examples/` instead.

## 7. Decisions for v0.1

- Channels: no single first channel. v0.1 ships every built-in channel
  (escrowed code bounties, grant programs, direct client work through
  the inbox) behind one channel interface, and research ranks across all
  of them. Each channel runs a liveness check at startup; a dead channel
  is skipped and logged, not fatal. New channels plug in by implementing
  the same interface.
- Payment proof: the payment notification email parser ships first,
  since it works for any bank, e-wallet, or platform that sends email.
  Many banks expose no transaction API for personal accounts, so this is
  the most widely usable source. Platform payout APIs and on-chain
  balance follow behind the same adapter interface.
- Plugin: ships inside this repo as `plugin/`, still usable on its own.
- Notifications: pluggable notifier with desktop notification, email,
  chat webhook (Telegram, Discord, Slack), and mobile push. Users enable
  any combination in config; approval requests and emergency stops go
  to every enabled notifier. Approval replies are accepted only from
  notifiers that support authenticated replies; the rest link to the
  local `outbox/` draft.

## 8. Open questions

- Approval reply security: how chat and push replies are authenticated
  so a forged message cannot approve an outward action.
- Liveness criteria per channel type (what "recently active, paying"
  means for bounties versus grants versus clients).
