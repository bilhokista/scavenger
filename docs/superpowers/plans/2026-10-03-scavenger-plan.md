# Scavenger — implementation plan

Date: 2026-10-03.
Spec: `docs/superpowers/specs/2026-10-03-scavenger-design.md` (read it
first; this plan implements it and does not restate its reasoning).

This plan is written for an implementing agent working without the
author present. Where the spec is ambiguous, this plan makes the call and
marks it **Decision**. If you hit an ambiguity this plan does not cover,
stop and write the question into `docs/superpowers/plans/QUESTIONS.md`
instead of guessing; continue with tasks that do not depend on it.

---

## 0. Rules for the implementer

1. **Test first, every task.** Write the listed tests, run them, see
   them fail, implement, see them pass. A task is done only when its
   acceptance checks pass.
2. **No network in tests.** All HTTP goes through `httpx`; tests use
   `respx` or recorded fixtures under `tests/fixtures/`. IMAP and SMTP are
   behind small client classes that tests replace with fakes. A test that
   opens a socket is a bug.
3. **No invented platforms.** Do not name, hardcode, or ship support for
   any bounty board, grant program, or job feed until Phase 6, and then
   only after checking live (on that day) that it is reachable, active,
   and paying. Record the check in `docs/channels/LIVENESS.md` with the
   date and the evidence URLs.
4. **No outward action outside `senders/`.** Code outside
   `daemon/scavenger/senders/` must never send email, open PRs, post,
   submit forms, or move money. A test enforces this (task 4.6).
5. **Every external call has a timeout** (default 20 s, from config) and
   its failure surfaces as a typed error, never a silent `None` or empty
   list. An adapter that cannot reach its source returns `unverified`.
6. **Money is `decimal.Decimal`**, stored in sqlite as TEXT. Never use
   `float` for money. Timestamps are timezone-aware UTC, stored as ISO
   8601 TEXT.
7. **Secrets come from environment variables only.** Config files hold
   the variable *names*, never values. Startup fails with a clear message
   if a required variable is missing.
8. **Commits:** one commit per task, Conventional Commits
   (`feat:`, `test:`, `docs:`, `chore:`, `ci:`), message body says what
   and why in plain prose. No AI co-author trailers. Run `ruff check`,
   `ruff format --check`, `pytest`, and `git diff --check` before every
   commit.
9. **Write like the surrounding code.** No filler comments, no
   restating what the code does. Comments explain why, where it is not
   obvious.

---

## 1. Stack and layout

- Python 3.11+ (needs `tomllib`).
- Runtime deps: `httpx`, `anthropic` (optional extra), `openai`
  (optional extra). Nothing else without a written reason in the commit.
- Dev deps: `pytest`, `pytest-cov`, `respx`, `ruff`, `freezegun`.
- Packaging: `pyproject.toml` at repo root, setuptools,
  `package-dir = {"" = "daemon"}`, package `scavenger`, console script
  `scavenger = scavenger.cli:main`.

Final tree (create directories as tasks need them, not all up front):

```
scavenger/
  pyproject.toml
  scavenger.example.toml
  SKILL.md
  README.md
  CONTRIBUTING.md
  LICENSE                      # MIT
  .gitignore
  .github/workflows/ci.yml
  daemon/scavenger/
    __init__.py
    cli.py
    config.py
    clock.py                   # now(); patched in tests
    money.py                   # Decimal parsing, fx conversion
    goal.py                    # GOAL.md parse/validate/render
    store.py                   # sqlite schema + all queries
    llm.py                     # provider-neutral LLM client
    verifier.py
    brake.py
    switcher.py
    executor.py
    research.py
    loop.py
    supervisor.py
    report.py                  # REPORT.md renderer (pure, reads store)
    outbox.py                  # drafts, tokens, standing limits
    compiler.py                # brief -> GOAL.md
    adapters/
      __init__.py              # Adapter protocol, ProofState, registry
      github.py
      inbox.py
      payment_email.py
    channels/
      __init__.py              # Channel protocol, registry
      bounty_board.py
      grants.py
      client_inbox.py
    senders/
      __init__.py              # Sender protocol, registry
      email_smtp.py
      github_pr.py
      github_comment.py
      manual.py
    notifiers/
      __init__.py              # Notifier protocol, fan-out
      desktop.py
      email.py
      telegram.py
      discord.py
      slack.py
      ntfy.py
  plugin/
    .claude-plugin/plugin.json
    hooks/hooks.json
    hooks/gate.py
    hooks/evidence.py
    hooks/session.py
    tests/
  tests/
    conftest.py
    fixtures/
    unit/
    integration/
  docs/
    conventions.md
    supervisor.md
    authoring-channels.md
    authoring-adapters.md
    authoring-notifiers.md
    channels/LIVENESS.md
    examples/demo-mission/GOAL.md
  missions/                    # gitignored, created by `scavenger init`
```

---

## 2. Shared definitions

These are referenced by every phase. Implement them exactly; if a phase
needs a change, change it here in the same commit.

### 2.1 Config: `scavenger.toml`

Loaded by `config.py` into frozen dataclasses. Unknown keys are an error
(catches typos). Paths are resolved relative to the config file.

```toml
[paths]
missions = "missions"
db = "ledger.sqlite3"
run_dir = ".scavenger"          # pid file, heartbeat, active mission

[llm]
provider = "anthropic"          # "anthropic" | "openai_compatible" | "none"
model = "claude-sonnet-5-5"
api_key_env = "ANTHROPIC_API_KEY"
base_url = ""                   # only for openai_compatible
max_tokens_per_call = 4000
price_per_million_input = "3.00"    # in [budget].currency, for spend accounting
price_per_million_output = "15.00"

[budget]
currency = "USD"                # currency budgets and spend are counted in

[fx]                            # static rates INTO the mission target currency
# "USD->IDR" = "16000"          # key format "FROM->TO"; missing pair = not counted

[http]
timeout_seconds = 20
user_agent = "scavenger/0.1 (+https://github.com/<owner>/scavenger)"

[loop]
tick_seconds = 60
max_active = 1                  # concurrently active (non-pending) strategies

[verifier]
unverified_backoff_minutes = [5, 30, 120]
check_interval_minutes = 30     # how often pending strategies are re-checked

[research]
max_strategies = 5
min_interval_minutes = 60       # anti-wandering: min gap between refills
max_tokens_per_run = 200000
gap_floor_ratio = "0.05"
web_search = "none"             # reserved; no provider in v0.1

[ledger]
min_history_for_probability = 3
block_after_same_cause_deaths = 2
block_days = 14

[outbox]
token_ttl_hours = 24
max_sends_per_day = 10
max_open_prs_per_repo = 3
duplicate_body_window_days = 7
secret_env = "SCAVENGER_APPROVAL_SECRET"   # >= 32 bytes, required

[supervisor]
max_hourly_money = "5.00"       # in [budget].currency
max_hourly_tokens = 500000
heartbeat_stale_factor = 3      # stale if older than factor * loop.tick_seconds
spin_rounds = 3

[executor]
agent_command = []              # e.g. ["claude", "-p"]; empty = no code work
agent_timeout_minutes = 60

[github]
token_env = "GITHUB_TOKEN"
username = ""

[imap]
host = ""
port = 993
username_env = "SCAVENGER_IMAP_USER"
password_env = "SCAVENGER_IMAP_PASSWORD"
folder = "INBOX"

[smtp]
host = ""
port = 587
username_env = "SCAVENGER_SMTP_USER"
password_env = "SCAVENGER_SMTP_PASSWORD"
from_address = ""

[[payment_rules]]               # one per sender that emails payment notices
name = "example-bank"
from_regex = "noreply@bank\\.example"
subject_regex = "(?i)incoming transfer"
amount_regex = "(?P<amount>[0-9.,]+)"
decimal_separator = "."         # "." or ","
currency = "USD"                # fixed currency for this sender, or ""
currency_regex = ""             # used when currency = ""; named group "currency"
reference_regex = "(?i)ref(?:erence)?[: ]+(?P<ref>\\S+)"

[notifiers]
enabled = ["desktop"]           # any of: desktop, email, telegram, discord, slack, ntfy

[notifiers.email]
to = ""

[notifiers.telegram]
bot_token_env = "SCAVENGER_TELEGRAM_TOKEN"
chat_id = ""
allowed_user_ids = []           # only these may approve

[notifiers.discord]
webhook_url_env = "SCAVENGER_DISCORD_WEBHOOK"

[notifiers.slack]
webhook_url_env = "SCAVENGER_SLACK_WEBHOOK"

[notifiers.ntfy]
server = "https://ntfy.sh"
topic_env = "SCAVENGER_NTFY_TOPIC"

[channels]
enabled = []                    # filled in Phase 6
```

### 2.2 GOAL.md format

TOML front matter between `+++` lines, then Markdown. Parsed by
`goal.py` with `tomllib`. The front matter is the contract; the body is
for humans.

```markdown
+++
name = "demo-mission"           # [a-z0-9-]{3,48}, equals directory name
created = 2026-10-03T05:00:00Z
statement = "Freelance developer has no income this month; rent of 1500 USD is due 2026-11-30."

[target]
amount = "1500.00"
currency = "USD"
deadline = 2026-11-30T16:59:59Z

[budget]
money = "50.00"                 # in [budget].currency of scavenger.toml
tokens = 2000000
rounds = 200                    # total failed rounds across all strategies

[settle]                        # the final proof: money arrived
adapter = "payment_email"
rules = ["example-bank"]        # names from [[payment_rules]]

[[ladder]]                      # 3-5 rungs, last one must be "settled"
rung = "submitted"
expected_wait_hours = 2
[[ladder]]
rung = "replied"
expected_wait_hours = 72
max_pending_hours = 168
[[ladder]]
rung = "settled"
expected_wait_hours = 336

[strategy_defaults]
max_rounds = 3                  # failed rounds before a strategy dies
max_loss = "10.00"              # money spent on one strategy before it dies
max_pending_hours = 168

[[assumptions]]
text = "Target currency is USD because no currency was given."
source = "silence"              # "silence" | "human"
+++

## Statement

Free text.

## Assumptions log

Free text mirror of [[assumptions]], appended over time.
```

Validation (`goal.validate(goal) -> list[GoalError]`), each a distinct
error code:

| Code | Condition |
|---|---|
| `missing_verifier` | `[settle]` absent, or `adapter` not registered, or a rule name not in config |
| `missing_budget` | `[budget]` absent or any of money/tokens/rounds missing or <= 0 |
| `missing_deadline` | `target.deadline` absent or not in the future at compile time |
| `missing_target` | amount <= 0 or currency not 3 uppercase letters |
| `bad_ladder` | fewer than 3 or more than 5 rungs, duplicate rung names, last rung not `settled` |
| `bad_name` | name pattern fails or differs from directory name |
| `unmeasurable_statement` | statement empty or contains no number (loss must be measurable) |

`missing_verifier`, `missing_budget`, `missing_deadline` are the three
never-assumed fields from spec section 3. The compiler may never fill
them from silence.

Rung names are free strings, but these have built-in meaning and map to
default adapters: `submitted`, `replied`, `accepted`, `pr_open`,
`pr_merged`, `deposit`, `settled`. A strategy declares per rung which
adapter and locator it uses (section 2.4).

### 2.3 Database schema (`store.py`)

Single sqlite file at `paths.db`. `PRAGMA foreign_keys = ON`,
`journal_mode = WAL`. Schema version in `schema_version` table; migrations
are a list of SQL strings applied in order inside one transaction each.
Version 1:

```sql
CREATE TABLE schema_version (version INTEGER NOT NULL);

CREATE TABLE missions (
  name            TEXT PRIMARY KEY,
  goal_path       TEXT NOT NULL,
  goal_sha256     TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN
                   ('active','done','impossible','stopped_budget',
                    'stopped_deadline','stopped_emergency','stopped_human')),
  target_amount   TEXT NOT NULL,
  target_currency TEXT NOT NULL,
  deadline        TEXT NOT NULL,
  empty_refills   INTEGER NOT NULL DEFAULT 0,
  last_research_at TEXT,
  created_at      TEXT NOT NULL,
  stopped_at      TEXT,
  stop_reason     TEXT
);

CREATE TABLE strategies (
  id                  INTEGER PRIMARY KEY,
  mission             TEXT NOT NULL REFERENCES missions(name),
  channel             TEXT NOT NULL,
  path                TEXT NOT NULL,          -- one-line human description
  path_key            TEXT NOT NULL,          -- normalized identity, see 2.6
  outward_key         TEXT NOT NULL,          -- account/repo/inbox it writes to
  payout_amount       TEXT NOT NULL,
  payout_currency     TEXT NOT NULL,
  guarantor           TEXT NOT NULL CHECK (guarantor IN
                       ('escrow','grant','signed_client','none')),
  guarantor_evidence  TEXT,                   -- URL; required unless 'none'
  capital_needed      TEXT NOT NULL,
  hours_first_proof   REAL NOT NULL,
  hours_settlement    REAL NOT NULL,
  probability         REAL NOT NULL,
  probability_source  TEXT NOT NULL CHECK (probability_source IN ('ledger','estimate')),
  probability_note    TEXT NOT NULL,
  score               TEXT NOT NULL,
  rungs_json          TEXT NOT NULL,          -- per-rung adapter + locator template
  status              TEXT NOT NULL CHECK (status IN
                       ('queued','awaiting_approval','active','pending',
                        'needs_human','dead','won')),
  rung_index          INTEGER NOT NULL DEFAULT 0,
  failed_rounds       INTEGER NOT NULL DEFAULT 0,
  loss                TEXT NOT NULL DEFAULT '0',
  pending_since       TEXT,
  unverified_streak   INTEGER NOT NULL DEFAULT 0,
  next_check_at       TEXT,
  created_at          TEXT NOT NULL,
  closed_at           TEXT,
  death_cause         TEXT CHECK (death_cause IN
                       ('round_limit','loss_limit','pending_timeout','rejected',
                        'channel_dead','human_rejected','mission_stopped')),
  death_evidence      INTEGER REFERENCES proof_checks(id)
);

CREATE TABLE rounds (
  id               INTEGER PRIMARY KEY,
  strategy_id      INTEGER NOT NULL REFERENCES strategies(id),
  started_at       TEXT NOT NULL,
  ended_at         TEXT,
  action_kind      TEXT NOT NULL,
  action_fingerprint TEXT NOT NULL,
  outbox_id        TEXT REFERENCES outbox(id),
  locator_json     TEXT,                      -- where the verifier should look
  result_state     TEXT CHECK (result_state IN ('pass','pending','fail','unverified'))
);

CREATE TABLE proof_checks (
  id              INTEGER PRIMARY KEY,
  strategy_id     INTEGER NOT NULL REFERENCES strategies(id),
  round_id        INTEGER REFERENCES rounds(id),
  rung            TEXT NOT NULL,
  adapter         TEXT NOT NULL,
  locator_json    TEXT NOT NULL,
  state           TEXT NOT NULL CHECK (state IN ('pass','pending','fail','unverified')),
  evidence_raw    TEXT NOT NULL,              -- raw fetched content, truncated to 64 KiB
  evidence_sha256 TEXT NOT NULL,
  error           TEXT,
  checked_at      TEXT NOT NULL
);

CREATE TABLE outbox (
  id              TEXT PRIMARY KEY,           -- uuid4 hex
  mission         TEXT NOT NULL REFERENCES missions(name),
  strategy_id     INTEGER REFERENCES strategies(id),
  kind            TEXT NOT NULL CHECK (kind IN
                   ('email','github_pr','github_comment','manual','strategy_start')),
  target          TEXT NOT NULL,              -- recipient / repo / URL
  body            TEXT NOT NULL,
  links_json      TEXT NOT NULL DEFAULT '[]',
  payload_json    TEXT NOT NULL DEFAULT '{}', -- kind-specific (patch path, subject, ...)
  cost            TEXT NOT NULL DEFAULT '0',
  content_sha256  TEXT NOT NULL,
  expires_at      TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN
                   ('awaiting','approved','rejected','expired','sent','failed')),
  decided_at      TEXT,
  decided_via     TEXT,                       -- 'cli' | 'telegram'
  decided_by      TEXT,
  sent_at         TEXT,
  send_result_json TEXT,
  created_at      TEXT NOT NULL
);

CREATE TABLE spend (
  id          INTEGER PRIMARY KEY,
  mission     TEXT NOT NULL REFERENCES missions(name),
  strategy_id INTEGER REFERENCES strategies(id),
  at          TEXT NOT NULL,
  tokens_in   INTEGER NOT NULL DEFAULT 0,
  tokens_out  INTEGER NOT NULL DEFAULT 0,
  money       TEXT NOT NULL DEFAULT '0',
  source      TEXT NOT NULL                   -- 'llm' | 'capital' | 'agent'
);

CREATE TABLE settlements (
  id              INTEGER PRIMARY KEY,
  mission         TEXT NOT NULL REFERENCES missions(name),
  strategy_id     INTEGER REFERENCES strategies(id),
  amount          TEXT NOT NULL,
  currency        TEXT NOT NULL,
  amount_in_target TEXT,                      -- NULL when no fx rate
  message_id      TEXT NOT NULL UNIQUE,       -- one email never counts twice
  proof_check_id  INTEGER NOT NULL REFERENCES proof_checks(id),
  at              TEXT NOT NULL
);

CREATE TABLE ledger (
  id             INTEGER PRIMARY KEY,
  mission        TEXT NOT NULL,
  strategy_id    INTEGER NOT NULL,
  channel        TEXT NOT NULL,
  path_key       TEXT NOT NULL,
  outcome        TEXT NOT NULL CHECK (outcome IN ('won','dead')),
  death_cause    TEXT,
  rung_reached   TEXT NOT NULL,
  failed_rounds  INTEGER NOT NULL,
  money_spent    TEXT NOT NULL,
  tokens         INTEGER NOT NULL,
  hours_to_rung_json TEXT NOT NULL,           -- {"submitted": 1.5, ...}
  closed_at      TEXT NOT NULL
);

CREATE TABLE channel_blocks (
  channel     TEXT PRIMARY KEY,
  cause       TEXT NOT NULL,
  until       TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE events (
  id       INTEGER PRIMARY KEY,
  at       TEXT NOT NULL,
  mission  TEXT,
  level    TEXT NOT NULL CHECK (level IN ('info','warn','error')),
  kind     TEXT NOT NULL,
  message  TEXT NOT NULL
);

CREATE INDEX idx_strategies_mission_status ON strategies(mission, status);
CREATE INDEX idx_proof_checks_strategy ON proof_checks(strategy_id, checked_at);
CREATE INDEX idx_spend_at ON spend(at);
CREATE INDEX idx_outbox_status ON outbox(status);
```

`store.py` exposes a `Store` class. Every other module uses its methods;
no other module runs SQL. Methods return frozen dataclasses, never raw
rows. Writes that change more than one table run in one transaction
(`with store.tx():`).

### 2.4 Interfaces

```python
# adapters/__init__.py
class ProofState(StrEnum):
    PASS = "pass"; PENDING = "pending"; FAIL = "fail"; UNVERIFIED = "unverified"

@dataclass(frozen=True)
class ProofResult:
    state: ProofState
    evidence_raw: str          # exactly what was fetched (truncated 64 KiB)
    detail: str                # one line, human readable
    settlement: Settlement | None = None   # only payment_email sets this

class Adapter(Protocol):
    name: str
    def check(self, rung: str, locator: Mapping[str, Any], since: datetime) -> ProofResult: ...

# channels/__init__.py
@dataclass(frozen=True)
class Liveness:
    alive: bool
    evidence_urls: tuple[str, ...]
    detail: str
    checked_at: datetime

@dataclass(frozen=True)
class Candidate:              # raw opportunity, before research ranks it
    channel: str
    path: str
    path_key: str
    outward_key: str
    payout_amount: Decimal
    payout_currency: str
    guarantor: Guarantor
    guarantor_evidence: str | None
    capital_needed: Decimal
    hours_first_proof: float
    hours_settlement: float
    source_urls: tuple[str, ...]
    rungs: tuple[RungPlan, ...]   # adapter + locator template per ladder rung

@dataclass(frozen=True)
class Action:
    kind: Literal["draft", "local", "wait"]
    draft: DraftSpec | None      # for kind="draft": becomes an outbox row
    description: str
    fingerprint: str             # sha256 of (kind, target, normalized body)

class Channel(Protocol):
    name: str
    def liveness(self) -> Liveness: ...
    def discover(self, goal: Goal) -> list[Candidate]: ...
    def next_action(self, strategy: Strategy, last_check: ProofCheck | None) -> Action: ...

# senders/__init__.py
@dataclass(frozen=True)
class SendResult:
    ok: bool
    locator: Mapping[str, Any]   # handed to the verifier, e.g. {"repo":..., "pr": 12}
    detail: str

class Sender(Protocol):
    kind: str                    # matches outbox.kind
    def send(self, item: OutboxItem) -> SendResult: ...

# notifiers/__init__.py
@dataclass(frozen=True)
class Notice:
    kind: Literal["approval_request", "emergency_stop", "mission_stop", "escalation"]
    title: str
    body: str
    outbox_id: str | None = None
    token: str | None = None      # only sent to notifiers that support replies

class Notifier(Protocol):
    name: str
    supports_replies: bool
    def notify(self, notice: Notice) -> None: ...      # raises NotifyError on failure
    def poll_replies(self) -> list[Reply]: ...          # [] when supports_replies is False

# llm.py
@dataclass(frozen=True)
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int

class LLM(Protocol):
    def complete(self, system: str, prompt: str, *, json_schema: dict | None = None) -> LLMResult: ...
```

`llm.py` ships `AnthropicLLM`, `OpenAICompatibleLLM`, `NoLLM` (raises on
call), and `tests/conftest.py` ships `FakeLLM` returning scripted
responses. Every LLM call records a `spend` row through the caller.

### 2.5 Strategy state machine

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

`won` and `dead` are terminal and write one `ledger` row each, in the
same transaction as the status change.

**Decision (round counting):** spec says "fail counts down to the
strategy limit". So `max_rounds` counts *failed* rounds only. `pass` and
`pending` do not consume it. Mission `budget.rounds` counts failed
rounds across all strategies.

**Decision (pending timeout):** max pending hours come from the current
rung's `max_pending_hours`, else `strategy_defaults.max_pending_hours`.

### 2.6 Keys and fingerprints

- `path_key`: `f"{channel}:{canonical_url_or_id}"`, lowercase, no
  query string, no trailing slash. Two candidates with the same
  `path_key` in one mission are the same strategy; research dedupes.
- `outward_key`: the account or resource written to, e.g.
  `github:owner/repo`, `email:alice@example.com`, `form:host`.
- `action_fingerprint`: sha256 hex of `kind + "\n" + target + "\n" +
  body` with whitespace collapsed and lowercased.
- `content_sha256` for outbox: sha256 hex of canonical JSON of
  `{kind, target, body, links, payload, cost}` with sorted keys.

### 2.7 Approval token

```
message = f"{outbox_id}|{content_sha256}|{expires_at_iso}".encode()
token   = base64.b32encode(hmac.new(secret, message, "sha256").digest())[:20].decode()
```

Verification recomputes from the *current* row; if the body changed,
`content_sha256` changed and the old token fails. Compare with
`hmac.compare_digest`. Tokens are never stored.

### 2.8 Ranking formula (`switcher.score`)

```
gap              = target - settled_in_target
payout_target    = fx(payout_amount, payout_currency -> target_currency)  # None -> drop
eta              = now + hours_settlement
if eta > deadline:               drop ("cannot settle before deadline")
if payout_target < gap * gap_floor_ratio:  drop ("below gap floor")
expected         = payout_target * probability
est_cost         = capital_needed (converted to budget currency)
                   + estimated tokens * blended token price
                   (estimated tokens = 50_000 per expected round * ladder length; constant in code)
score            = expected / max(est_cost, Decimal("0.01"))
```

Sort: guarantor `none` last; then score descending; then
`hours_first_proof` ascending; then `capital_needed` ascending; then
`path_key` ascending (deterministic).

**Probability:** if the ledger has at least
`ledger.min_history_for_probability` closed rows for the channel,
`p = (won + 1) / (n + 2)` and source `ledger`. Otherwise the research
estimate, clamped to `[0.01, 0.90]`, source `estimate`, with the LLM's
one-sentence reason stored in `probability_note`.

### 2.9 Channel blocking

When a strategy dies, count ledger rows for the same `channel` and
`death_cause` within the last `block_days`. If the count reaches
`block_after_same_cause_deaths`, upsert `channel_blocks` with
`until = now + block_days`. Research skips blocked channels.
`scavenger unblock <channel>` deletes the row. A passing liveness check
does not unblock by itself.

**Decision:** spec says "blocked until data shows the cause changed".
v0.1 approximates this with a time window plus manual unblock, because
"cause changed" has no general machine test.

---

## 3. Phases

Phases run in order. Inside a phase, tasks run in order. Each task lists
files, tests (names are the test function names to write), and
acceptance.

### Phase 0 — skeleton

**Task 0.1 — project scaffolding**
- Files: `pyproject.toml`, `.gitignore`, `LICENSE` (MIT, copyright
  holder placeholder `<copyright holder>`), `daemon/scavenger/__init__.py`
  (`__version__ = "0.1.0.dev0"`), `daemon/scavenger/cli.py` (argparse,
  `--version` only), `tests/conftest.py`, `tests/unit/test_cli.py`.
- `.gitignore` must contain: `missions/`, `ledger.sqlite3*`,
  `.scavenger/`, `.env`, `__pycache__/`, `.pytest_cache/`, `.coverage`,
  `dist/`, `*.egg-info/`.
- Tests: `test_version_flag_prints_version`.
- Acceptance: `pip install -e .[dev]` then `scavenger --version` prints
  `0.1.0.dev0`; `pytest` green.

**Task 0.2 — CI**
- File: `.github/workflows/ci.yml`. Matrix: ubuntu-latest,
  windows-latest; Python 3.11 and 3.12. Steps: install, `ruff check`,
  `ruff format --check`, `pytest --cov=scavenger --cov-fail-under=80`.
  The coverage gate starts at 0 and is raised to 80 in task 8.1.
- Acceptance: workflow file passes `actionlint` if available locally;
  otherwise reviewed by eye. Do not push to verify.

**Task 0.3 — conventions doc**
- File: `docs/conventions.md` containing section 0 of this plan
  rewritten as contributor rules, plus sections 2.5 to 2.9 as reference.

### Phase 1 — foundations

**Task 1.1 — clock and money**
- Files: `clock.py` (`now() -> datetime`, UTC aware), `money.py`.
- `money.parse_amount(text: str, decimal_separator: str) -> Decimal`:
  strips currency symbols and spaces; with separator `.`, removes `,`
  thousands; with `,`, removes `.` thousands and swaps `,` to `.`.
  Raises `AmountError` on anything else.
- `money.convert(amount, from_ccy, to_ccy, fx) -> Decimal | None`:
  same currency returns amount; missing pair returns `None`.
- Tests: `test_parse_dot_decimal_with_comma_thousands`,
  `test_parse_comma_decimal_with_dot_thousands`,
  `test_parse_rejects_two_decimal_marks`,
  `test_parse_rejects_letters`, `test_convert_same_currency`,
  `test_convert_missing_pair_returns_none`,
  `test_convert_uses_decimal_not_float`.

**Task 1.2 — config loader**
- File: `config.py`. `load(path) -> Config`. Frozen dataclasses mirroring
  section 2.1. `Config.require_env(name) -> str` raises `ConfigError`
  naming the variable.
- Tests: `test_loads_example_config`, `test_unknown_key_is_error`,
  `test_paths_resolve_relative_to_config_file`,
  `test_require_env_missing_names_variable`,
  `test_money_fields_are_decimal`.
- Also create `scavenger.example.toml` exactly as section 2.1.

**Task 1.3 — store**
- File: `store.py` implementing schema 2.3 and these methods (add more
  only when a later task needs them, in that task):
  `open(path)`, `tx()`, `create_mission`, `get_mission`,
  `set_mission_status`, `add_strategy`, `get_strategy`,
  `list_strategies(mission, statuses)`, `update_strategy`,
  `start_round`, `end_round`, `add_proof_check`, `last_proof_checks(strategy_id, n)`,
  `add_spend`, `spend_since(mission | None, since)`,
  `add_settlement` (returns `False` on duplicate `message_id`),
  `settled_in_target(mission)`, `add_outbox`, `get_outbox`,
  `set_outbox_status`, `sends_since(since)`, `close_strategy`
  (status change + ledger row in one transaction), `ledger_for_channel`,
  `block_channel`, `unblock_channel`, `active_block(channel)`,
  `add_event`, `write_heartbeat`, `read_heartbeat`.
  Heartbeat lives in `run_dir/heartbeat` as an ISO timestamp file, not
  in sqlite, so a locked DB cannot fake liveness.
- Tests: `test_migrations_create_all_tables`,
  `test_reopen_does_not_reapply_migrations`,
  `test_foreign_keys_enforced`, `test_money_roundtrips_as_decimal`,
  `test_close_strategy_writes_ledger_atomically` (inject failure after
  status update; assert neither change persisted),
  `test_duplicate_settlement_message_id_rejected`,
  `test_settled_in_target_ignores_null_fx_rows`,
  `test_spend_since_filters_by_time`.

**Task 1.4 — GOAL.md parse, validate, render**
- File: `goal.py`. `parse(path) -> Goal`, `validate(goal, config, now) ->
  list[GoalError]`, `render(goal) -> str` (round-trips:
  `parse(render(g)) == g`), `append_assumption(path, text, source)`.
- Tests: one test per error code in section 2.2
  (`test_missing_verifier_detected`, ...), plus
  `test_valid_example_has_no_errors`, `test_render_roundtrip`,
  `test_append_assumption_preserves_body`,
  `test_front_matter_without_closing_delimiter_is_error`.
- Also create `docs/examples/demo-mission/GOAL.md` (section 2.2 sample)
  and a test that it validates against `scavenger.example.toml`.

**Task 1.5 — LLM client**
- File: `llm.py` per section 2.4. Anthropic and OpenAI-compatible
  clients are imported lazily so the base install works without them.
  `json_schema` requests are validated after the call: invalid JSON
  raises `LLMFormatError` after one retry.
- Tests (with `FakeLLM` and mocked SDK clients):
  `test_fake_llm_returns_scripted`, `test_json_schema_invalid_retries_once`,
  `test_token_counts_reported`, `test_no_llm_raises`.

### Phase 2 — verifier and adapters

**Task 2.1 — adapter protocol and registry**
- File: `adapters/__init__.py`. `register(adapter)`, `get(name)`.
  Unknown name raises `UnknownAdapter`.
- Tests: `test_register_and_get`, `test_unknown_adapter_raises`.

**Task 2.2 — GitHub adapter**
- File: `adapters/github.py`. Uses REST API v3 with `GITHUB_TOKEN`.
- Locator forms and rung behavior:

| Rung | Locator | pass | pending | fail |
|---|---|---|---|---|
| `pr_open` / `submitted` | `{"repo": "o/r", "pr": N}` | PR exists | — | PR not found (404) |
| `pr_merged` / `accepted` | `{"repo": "o/r", "pr": N}` | `merged == true` | open | closed and not merged |
| `replied` | `{"repo": "o/r", "pr": N, "author": "me"}` | a comment or review by someone other than `author` created after `since` | none yet | — |
| `replied` | `{"repo": "o/r", "issue": N, "author": "me"}` | same, on the issue | none yet | — |

- Network error, 5xx, or rate limit (403 with `X-RateLimit-Remaining: 0`)
  returns `unverified`, never `fail`.
- `evidence_raw` is the JSON body fetched.
- Tests with `respx`: one per table cell, plus
  `test_rate_limit_is_unverified`, `test_timeout_is_unverified`,
  `test_evidence_is_raw_response`.

**Task 2.3 — inbox adapter**
- File: `adapters/inbox.py`. `ImapClient` wrapper class (connect, search,
  fetch headers+text) so tests inject `FakeImap`.
- Locator: `{"thread_message_id": "<id@host>", "counterparty": "a@b.c",
  "require": "any_reply" | "positive_reply"}`.
- pass: a message from `counterparty` with `In-Reply-To` or
  `References` containing `thread_message_id`, dated after `since`; and,
  for `positive_reply`, the LLM classifier says `positive`.
- fail: `positive_reply` and classifier says `negative`.
- pending: no such message yet.
- unverified: IMAP error.
- Classifier: LLM with `json_schema = {"label": "positive"|"negative"|"unclear"}`,
  input is the raw message text only (strip quoted history below the
  first `On ... wrote:` or `>` block). `unclear` maps to `pending` and an
  `escalation` event.
- Tests: `test_reply_from_counterparty_passes`,
  `test_reply_from_other_sender_ignored`,
  `test_reply_before_since_ignored`, `test_positive_reply_required_negative_fails`,
  `test_unclear_classification_is_pending`, `test_imap_error_unverified`,
  `test_classifier_input_excludes_executor_text` (assert the prompt
  passed to FakeLLM contains only the fetched message).

**Task 2.4 — payment email adapter**
- File: `adapters/payment_email.py`. Uses `ImapClient`.
- Locator: `{"rules": ["example-bank"], "expected_amount": "100.00",
  "currency": "USD", "reference": "INV-12" | null,
  "tolerance": "0.01"}`.
- For each message since `since` whose From matches a rule's
  `from_regex` and Subject matches `subject_regex`: extract amount
  (`money.parse_amount` with the rule's separator), currency, reference.
  Match when amount is within tolerance of expected and currency equals
  and, if `reference` is set, the extracted reference equals it.
- pass returns `settlement = Settlement(amount, currency, message_id)`.
  pending when no match. unverified on IMAP error. A matching email
  whose `message_id` is already in `settlements` is ignored (store
  check happens in the verifier, task 2.5).
- Tests: `test_matching_notice_passes_with_settlement`,
  `test_amount_outside_tolerance_pending`,
  `test_wrong_reference_pending`, `test_comma_decimal_rule`,
  `test_sender_not_in_rules_ignored`, `test_currency_from_regex`,
  `test_unparseable_amount_logged_not_passed`.

**Task 2.5 — verifier**
- File: `verifier.py`. `Verifier(store, config, adapters, clock)`.
  `check_strategy(strategy_id) -> ProofResult`:
  1. Load strategy and its current rung plan from `rungs_json`.
  2. Build locator: the rung's locator template merged with the
     `locator_json` of the latest round. **Only** these two sources.
     The function signature takes no executor output.
  3. Call `adapter.check(rung, locator, since=round.started_at or strategy.created_at)`.
  4. Store `proof_checks` row with raw evidence and sha256.
  5. Apply the state machine (2.5): pass climbs or wins; settlement on
     pass is inserted with fx conversion (duplicate `message_id` → treat
     as `pending`, add `warn` event); pending sets `pending_since` if
     unset and checks timeout; fail increments `failed_rounds`;
     unverified increments `unverified_streak` and sets `next_check_at`
     from the backoff list; third consecutive → `needs_human` and an
     `escalation` notice. Any non-unverified result resets the streak.
  6. Return the result.
- `due_strategies(mission) -> list[int]`: statuses `active` or `pending`
  with `next_check_at <= now` or null.
- Tests: `test_pass_climbs_rung`, `test_pass_on_last_rung_wins_and_writes_ledger`,
  `test_fail_increments_and_kills_at_limit`,
  `test_pending_sets_pending_since_once`,
  `test_pending_timeout_counts_as_fail`,
  `test_unverified_backoff_schedule`,
  `test_third_unverified_moves_to_needs_human_and_notifies`,
  `test_settlement_recorded_with_fx`, `test_settlement_without_fx_recorded_null`,
  `test_duplicate_settlement_treated_as_pending`,
  `test_verifier_signature_has_no_executor_input` (inspect signature),
  `test_adapter_receives_only_template_and_round_locator`.

### Phase 3 — brakes and supervisor

**Task 3.1 — brakes**
- File: `brake.py`. Pure functions over store reads:
  `strategy_verdict(strategy, goal) -> Death | None` (round_limit,
  loss_limit; pending timeout is in the verifier) and
  `mission_verdict(mission, goal, store, now) -> MissionStop | None`:
  `done` when `settled_in_target >= target`; `stopped_deadline` when
  `now > deadline`; `stopped_budget` when total money spend >= budget
  money, or tokens >= budget tokens, or total failed rounds >= budget
  rounds; `impossible` when `empty_refills >= 2` and no strategy is
  `pending`, `active`, `queued`, or `awaiting_approval`.
  Check order: done, deadline, budget, impossible (done wins ties).
- Tests: one per verdict, plus `test_done_wins_over_deadline_same_tick`,
  `test_budget_counts_llm_and_capital_spend`.

**Task 3.2 — report writer**
- File: `report.py`. `render_report(store, mission_name) -> str` and
  `write_report(store, mission_name, missions_dir)`. Pure reads.
- Sections, in order: Outcome (status, stop reason, settled vs target
  with percentage), Strategies (table: id, channel, path, status, rung
  reached, failed rounds, loss, death cause, evidence check id), Spend
  (money, tokens in/out), Settlements, Ledger lessons (channels blocked,
  causes), Assumptions (from GOAL.md).
- Tests: `test_report_contains_settled_vs_target`,
  `test_report_lists_every_strategy`, `test_report_marks_failed_mission_when_zero_settled`
  (spec section 1: activity without settled money is a failed mission),
  snapshot test against `tests/fixtures/report_expected.md`.

**Task 3.3 — supervisor**
- File: `supervisor.py`. Allowed imports from this package: `config`,
  `clock`, `store`, `report`, `notifiers`, `money`. Nothing else.
- `run_once(config) -> SupervisorResult`:
  1. Read heartbeat. Stale if older than
     `heartbeat_stale_factor * loop.tick_seconds`.
  2. Spend in last hour (money, tokens) from `spend`.
  3. Spin: for each active mission, the last `spin_rounds` rounds of any
     strategy share one `action_fingerprint` and one non-pending
     `result_state`.
  4. If any trigger fires: read pid from `run_dir/daemon.pid`, terminate
     the process (`os.kill` with SIGTERM; on Windows `taskkill /PID <pid> /F`
     via subprocess), set affected missions to `stopped_emergency` with
     the trigger as reason, write REPORT.md, send `emergency_stop` notice
     to all notifiers. Stale heartbeat alone sends a notice and kills
     only if the pid still exists.
  5. Return what happened; exit code 0 when quiet, 3 when it acted.
- Tests: `test_supervisor_imports_are_restricted` (parse the module AST,
  assert the import set), `test_quiet_when_all_normal`,
  `test_hourly_money_trigger`, `test_hourly_token_trigger`,
  `test_spin_trigger_same_fingerprint_same_state`,
  `test_pending_rounds_do_not_count_as_spin`,
  `test_emergency_writes_report_and_notifies` (fake notifier),
  `test_stale_heartbeat_notifies`, `test_kill_uses_pid_file` (monkeypatch
  the kill function).

**Task 3.4 — scheduler docs**
- File: `docs/supervisor.md` with copy-paste setup for: cron
  (`*/5 * * * * scavenger supervise --once --config /path/scavenger.toml`),
  systemd service + timer units, Windows Task Scheduler (`schtasks /Create`
  command every 5 minutes). Explain that it must not run inside the
  daemon process.

### Phase 4 — notifiers, outbox, senders, approval gate

**Task 4.1 — notifier protocol and fan-out**
- File: `notifiers/__init__.py`. `fan_out(notice, notifiers)`: calls
  every enabled notifier; one failing notifier never stops the others;
  failures recorded as `error` events. If all fail, raise
  `AllNotifiersFailed` (the caller logs and keeps going; emergency stops
  still write REPORT.md).
- Tests: `test_one_failure_does_not_block_others`,
  `test_all_failures_raise`, `test_token_only_sent_to_reply_capable`.

**Task 4.2 — notifiers**
- Files and behavior (all HTTP via `httpx`, all fail with `NotifyError`):
  - `desktop.py`: Linux `notify-send`, macOS `osascript -e 'display
    notification ...'`, Windows PowerShell toast via
    `Windows.UI.Notifications`. Unsupported platform or missing binary:
    `warn` event, no exception. `supports_replies = False`.
  - `email.py`: SMTP via `smtplib` wrapper class (fakeable).
    `supports_replies = False`.
  - `telegram.py`: `sendMessage` to `chat_id`; `poll_replies` uses
    `getUpdates` with stored offset in `run_dir/telegram_offset`.
    Accepts only messages from `allowed_user_ids` matching
    `^/(approve|reject) ([0-9a-f]{32}) ([A-Z2-7]{20})$`.
    `supports_replies = True`.
  - `discord.py`, `slack.py`: incoming webhook POST. `supports_replies = False`.
  - `ntfy.py`: POST to `server/topic`. `supports_replies = False`.
- Notices without reply support include the line
  `Approve locally: scavenger approve <id>` and never include the token.
- Tests per notifier with `respx` or fakes; for Telegram:
  `test_reply_from_unlisted_user_ignored`,
  `test_malformed_command_ignored`, `test_offset_persisted`.

**Task 4.3 — outbox and tokens**
- File: `outbox.py`. `create_draft(spec, mission, strategy_id) ->
  OutboxItem`: writes `missions/<name>/outbox/<id>.md` (human readable:
  kind, target, cost, links, full body) and the `outbox` row, computes
  `content_sha256` and `expires_at`, runs standing limits (4.4), then
  sends an `approval_request` notice. `token_for(item)`,
  `verify_token(item, token) -> bool`, `approve(id, token | None, via,
  by)`, `reject(id, via, by)`, `expire_old()`.
  CLI approval (`via="cli"`) needs no token but still re-verifies that
  the file on disk hashes to `content_sha256`. If the `.md` file was
  edited, the CLI approval is refused with a message to run
  `scavenger redraft <id>`, which creates a new draft from the edited
  file and expires the old one.
- Tests: `test_token_roundtrip`, `test_token_fails_after_body_change`,
  `test_token_fails_after_expiry`, `test_token_compare_is_constant_time`
  (assert `hmac.compare_digest` is used, via monkeypatch spy),
  `test_cli_approve_refuses_edited_file`, `test_redraft_expires_old`,
  `test_missing_secret_fails_startup`, `test_secret_shorter_than_32_bytes_fails`.

**Task 4.4 — standing limits**
- In `outbox.py`, `check_limits(spec, store, github) -> list[LimitViolation]`,
  run at draft creation and again right before send:
  - `max_open_prs_per_repo`: for `github_pr`, count open PRs by
    `github.username` in the target repo via the search API; refuse at
    the limit.
  - Guarantor proof: for strategies with guarantor other than `none`,
    `guarantor_evidence` URL must return HTTP 200 now; else refuse.
  - `max_sends_per_day`: count `sent` rows in the last 24 h.
  - Duplicate body: same `action_fingerprint` body part sent to a
    different target within `duplicate_body_window_days` → refuse (no
    bulk outreach).
- A violation marks the draft `rejected` with `decided_via = "limit"`
  and records an event; it never reaches the human.
- Tests: one per limit, plus `test_limits_rechecked_at_send_time`.

**Task 4.5 — senders**
- Files:
  - `email_smtp.py`: sends `body` with `payload.subject` to `target`
    from `smtp.from_address`; returns locator
    `{"thread_message_id": <generated Message-ID>, "counterparty": target}`.
  - `github_pr.py`: payload carries `{"repo", "base", "branch",
    "title", "patch_path"}`. Uses a fork under `github.username`
    (create via API if absent), applies the patch in a temp clone with
    `git`, pushes the branch to the fork, opens the PR. Returns
    `{"repo": repo, "pr": number}`. Any git failure → `ok=False`, no
    partial PR.
  - `github_comment.py`: posts `body` on `payload.issue_url`. Returns
    `{"repo", "issue", "author": github.username}`.
  - `manual.py`: does nothing outward. Marks the item `sent` only when
    the human runs `scavenger mark-sent <id> --locator '<json>'`; used for
    forms, applications, payments, anything without an API.
- `send_approved(store, senders, outbox_limits)`: for each `approved`
  item, re-run limits, call the sender, record `sent`/`failed`, end the
  round with the returned locator.
- Tests with fakes (no real git push in unit tests; one integration test
  uses a local bare repo as "remote"): `test_email_sender_returns_message_id`,
  `test_github_pr_applies_patch_and_returns_number`,
  `test_github_pr_git_failure_no_pr`, `test_manual_requires_mark_sent`,
  `test_unapproved_items_never_sent`.

**Task 4.6 — outward isolation test**
- Test `tests/unit/test_outward_isolation.py`: walk the AST of every
  module under `daemon/scavenger/` except `senders/`, `notifiers/`,
  `adapters/`, `channels/`, `llm.py`; fail if any imports `smtplib`,
  calls `httpx` methods `post`/`put`/`patch`/`delete`, or runs
  `subprocess` with `git push`. Adapters and channels may use `httpx.get`
  only; assert they never call `post`/`put`/`patch`/`delete`
  (exception: GraphQL search endpoints if a channel needs them, listed
  explicitly in the test with a comment).

### Phase 5 — executor, switcher, loop

**Task 5.1 — switcher**
- File: `switcher.py`. `rank(candidates, goal, store, config, now) ->
  list[RankedCandidate]` implementing 2.8 and dropping with a reason
  (dropped candidates are logged as `info` events). `pick(mission) ->
  list[int]`: returns strategy ids to make `active` this tick, honoring
  `loop.max_active` and distinct `outward_key` among active ones.
  Strategies in `pending`, `awaiting_approval`, `needs_human` are not
  runnable. `needs_refill(mission) -> bool`: no `queued` or `active`
  strategies and `last_research_at` older than
  `research.min_interval_minutes`.
- Tests: `test_score_formula_matches_spec`,
  `test_drop_when_cannot_settle_before_deadline`,
  `test_drop_below_gap_floor`, `test_guarantor_none_ranked_last`,
  `test_tie_breaks_deterministic`, `test_ledger_probability_laplace`,
  `test_estimate_probability_clamped`, `test_missing_fx_dropped`,
  `test_pick_respects_max_active`, `test_pick_skips_same_outward_key`,
  `test_pending_not_runnable`, `test_needs_refill_respects_interval`.

**Task 5.2 — executor**
- File: `executor.py`. `run_round(strategy_id)`:
  1. Brake check (`brake.strategy_verdict`); dead → close and return.
  2. Skip if the strategy has an outbox item in `awaiting` or
     `approved` (one round, one proof).
  3. `action = channel.next_action(strategy, last_check)`.
  4. `start_round` with `action_kind` and `fingerprint`.
  5. `draft` → `outbox.create_draft`; round stays open until sent.
     `local` → run work (5.3) and end the round with its locator.
     `wait` → end the round without a state; nothing else.
  6. Record LLM spend for the round.
- Tests: `test_round_skipped_while_awaiting_approval`,
  `test_draft_action_creates_outbox_not_send`,
  `test_dead_strategy_not_executed`, `test_spend_recorded`.

**Task 5.3 — local work through an external agent**
- In `executor.py`, `run_agent(strategy, instructions) -> AgentResult`.
  Runs `executor.agent_command + [prompt]` in
  `missions/<name>/work/<strategy_id>/` with timeout
  `agent_timeout_minutes`. The prompt states: do the work, write a patch
  to `out/patch.diff` and a PR description to `out/pr.md`, never push,
  never send anything. After exit, the executor reads `out/` and builds a
  `github_pr` draft. A missing or empty patch is a failed round with the
  agent's stdout tail stored as evidence. Empty `agent_command` → the
  channel must not produce `local` actions; raise `ConfigError` if it
  does.
- Tests: `test_agent_timeout_fails_round`,
  `test_missing_patch_fails_round`, `test_patch_becomes_pr_draft`,
  `test_agent_runs_in_workspace_dir` (fake command is a Python script
  in `tests/fixtures/`).

**Task 5.4 — loop**
- File: `loop.py`. `tick(now)`, in this order, per active mission:
  1. `brake.mission_verdict` → on stop: mark remaining strategies
     `dead(mission_stopped)`, set mission status, write REPORT.md,
     `mission_stop` notice, continue to next mission.
  2. Poll replies from reply-capable notifiers → `outbox.approve/reject`.
  3. `outbox.expire_old()`; expired drafts end their round as `fail`.
  4. `send_approved(...)`.
  5. `verifier.check_strategy` for every due strategy.
  6. Channel liveness for channels with live strategies, at most once per
     hour per channel; dead channel → its strategies `dead(channel_dead)`.
  7. If `switcher.needs_refill`: `research.refill(mission)`.
  8. `switcher.pick` → for each, `executor.run_round`.
  9. `store.write_heartbeat`.
- `run(config)`: writes `run_dir/daemon.pid`, refuses to start if the pid
  file points to a live process, loops `tick` every `tick_seconds`,
  catches exceptions per mission (logs `error` event, continues), removes
  the pid file on clean exit.
- Integration test `tests/integration/test_full_mission.py` with
  FakeLLM, a fake channel, fake adapters, and fake senders, driving the
  clock with `freezegun`. One mission must go through, in order: research
  refill producing 3 strategies; strategy A sends a draft, gets approved,
  goes `pending`, times out, dies (`pending_timeout` on its 3rd failure);
  strategy B is worked while A is pending; B is rejected by the human →
  dead; queue empties → second refill; strategy C climbs all rungs,
  payment email settles 60% of target; a second settlement from C's
  follow-up reaches 100% → mission `done`; REPORT.md shows settled vs
  target and both deaths with evidence ids.
- Second integration test: budget stop mid-strategy writes REPORT.md
  and marks the open strategy `dead(mission_stopped)`.
- Third: two empty refills with nothing pending → `impossible`.

### Phase 6 — research and channels

**Task 6.0 — live channel survey (no code)**
- On the day this task starts, search for live platforms in each of the
  three channel types. For each candidate record in
  `docs/channels/LIVENESS.md`: name, URL, public API docs URL, evidence
  of a payout in the last 30 days (link), whether payout status is
  machine-readable, terms of service on automated access (link and the
  relevant sentence). Include only platforms whose terms allow API
  access for this use. If a type has zero qualifying platforms, write
  that down; its channel still ships with the generic part only.
- Acceptance: the file exists with dated evidence. Stop and write to
  `QUESTIONS.md` if no platform qualifies in any type.

**Task 6.1 — channel protocol and registry**
- File: `channels/__init__.py` per 2.4, registry like adapters, and
  `liveness_all(channels) -> dict[str, Liveness]` used at startup:
  dead channels are skipped with a `warn` event, never fatal.
- Tests: `test_dead_channel_skipped_not_fatal`, `test_registry`.

**Task 6.2 — bounty board channel**
- File: `channels/bounty_board.py`. Generic channel with one provider
  class per platform qualified in 6.0 (`providers/<platform>.py` inside
  the channel module directory if more than one).
- `discover`: open bounties with amount, currency, repo, issue URL,
  escrow evidence URL. Skip any issue that already has 2 or more open
  PRs with passing CI from other authors (spec repo rule set). Skip
  repos where `github.username` already has
  `max_open_prs_per_repo` open PRs.
- Rungs: `pr_open` (github adapter), `pr_merged` (github), `settled`
  (payment_email, plus the platform payout API adapter if 6.0 found one;
  write it as an extra adapter in `adapters/`).
- `next_action`: rung 0 → `local` action (agent writes patch) then
  `github_pr` draft; rung 1 → `wait`, or a `github_comment` draft only
  when a maintainer asked a question (detected by the github adapter
  evidence); rung 2 → `wait`.
- Tests with recorded fixtures from the qualified provider.

**Task 6.3 — grants channel**
- File: `channels/grants.py`. Providers qualified in 6.0 plus a generic
  provider reading grant rounds from configured RSS/Atom URLs
  (`[channels.grants] feeds = [...]`).
- `discover`: open rounds with deadline in the future and an amount.
  Guarantor `grant`, evidence = round page URL.
- Rungs: `submitted` (manual sender: application drafted, human submits,
  `mark-sent` with the application URL), `accepted` (inbox adapter
  `positive_reply` from the program's address), `settled`
  (payment_email).
- `next_action`: rung 0 → `manual` draft containing the full application
  text produced by the LLM from GOAL.md and the round requirements.
- Tests with fixtures.

**Task 6.4 — client inbox channel**
- File: `channels/client_inbox.py`. Two sources: inbound emails in the
  configured IMAP folder matching `[channels.client_inbox] filters`
  (subject/body regexes describing work requests), and configured job
  feeds (`feeds = [...]`, RSS/Atom). No cold outreach to addresses that
  did not publish a request: every candidate must cite the inbound
  message or the public posting.
- Rungs: `submitted` (email sent, or manual for feed postings that need a
  form), `replied` (inbox `positive_reply`), `deposit` (payment_email,
  partial amount from the proposal), `settled` (payment_email).
- Guarantor: `none` until a signed agreement link is recorded with
  `scavenger guarantor <strategy> signed_client <url>`; strategies with
  `none` require approval to start (spec section 4).
- Tests with fixtures, including `test_no_candidate_without_cited_request`.

**Task 6.5 — research**
- File: `research.py`. `refill(mission)`:
  1. Read ledger and blocks; skip blocked channels.
  2. `discover` on each live, unblocked channel; dedupe by `path_key`
     against all strategies ever created for this mission.
  3. One LLM call per candidate batch (max 20 candidates per call) to
     fill `probability` and `probability_note` only for channels without
     ledger history; schema-validated. Every other field comes from the
     channel, not the LLM.
  4. `switcher.rank`, keep the top `research.max_strategies`.
  5. Insert as `queued`, or `awaiting_approval` with a `strategy_start`
     outbox draft when guarantor is `none`.
  6. Zero kept → `empty_refills += 1`; else reset to 0. Set
     `last_research_at`.
  7. Stop early if tokens used this run exceed
     `research.max_tokens_per_run`.
- Tests: `test_blocked_channel_skipped`, `test_dedupe_against_history`,
  `test_llm_only_fills_probability`, `test_guarantor_none_needs_approval`,
  `test_empty_refill_counter`, `test_token_cap_stops_run`,
  `test_channel_block_after_two_same_cause_deaths`.

### Phase 7 — brief compiler, CLI, skill, plugin

**Task 7.1 — brief compiler**
- File: `compiler.py`. `compile_brief(brief, ask, config) -> Goal`:
  `ask(question) -> str | None` is injected (CLI uses stdin with a
  timeout; tests use a script). Steps:
  1. LLM drafts a Goal from the brief (schema-validated).
  2. Run `goal.validate`. Collect questions: one per error, never-assumed
     fields first. Ask at most 3 in total.
  3. Answers patch the draft (LLM call with the answer, schema-validated).
     Silence (`None`) on a non-never-assumed field → keep the LLM value
     and add an `[[assumptions]]` entry with `source = "silence"`.
  4. After 3 questions, if any never-assumed field still fails, raise
     `MissingRequiredField(field)`; the CLI exits 2 and prints one
     question for that field only.
  5. Write `missions/<name>/GOAL.md` and create the mission row.
- Tests: `test_never_assumed_field_not_filled_by_silence`,
  `test_at_most_three_questions`, `test_silence_logs_assumption`,
  `test_missing_required_after_three_raises`,
  `test_goal_written_before_any_other_mission_file` (no outbox, no work
  dir exists after compile).

**Task 7.2 — CLI**
- File: `cli.py`, argparse subcommands, every one takes `--config`
  (default `./scavenger.toml`):
  `init` (writes config from example if absent, creates dirs),
  `compile "<brief>"`, `validate <mission>`, `run [--once]`,
  `status [<mission>]`, `approve <id> [--token T]`, `reject <id>`,
  `redraft <id>`, `mark-sent <id> --locator JSON`,
  `guarantor <strategy> <kind> <url>`, `resume <strategy>`,
  `stop <mission>` (status `stopped_human`, writes report),
  `report <mission>`, `supervise --once`, `liveness`, `unblock <channel>`.
- Exit codes: 0 ok, 1 error, 2 needs human input, 3 supervisor acted.
- Tests: one smoke test per subcommand with a temp config and fakes.

**Task 7.3 — SKILL.md**
- File: `SKILL.md` at repo root, with front matter `name: scavenger`,
  `description:` one sentence on compiling a vague money brief into a
  GOAL.md contract. Body: the compiler procedure from 7.1 written as
  agent instructions, the GOAL.md template from 2.2, the three
  never-assumed fields, the 3-question limit, and "run
  `scavenger validate <mission>` if the CLI is installed; otherwise check
  the validation table by hand". No references to any specific agent
  product beyond a neutral note that it works with any agent that loads
  skills.
- Test: `tests/unit/test_skill_template.py` extracts the GOAL.md
  template from SKILL.md and asserts it parses and validates.

**Task 7.4 — plugin**
- Claude Code plugin under `plugin/`. Hook scripts are standalone,
  stdlib-only Python (must work without the daemon installed). Before
  writing, check the current Claude Code hooks documentation for the
  exact JSON input and output shapes and event names; adjust these
  details if they changed.
- `plugin/.claude-plugin/plugin.json`: name `scavenger`, version,
  description.
- `plugin/hooks/hooks.json`:
  - `SessionStart` → `session.py`: injects a short reminder: check
    available skills before acting; if the user gives a money brief,
    compile GOAL.md first.
  - `PreToolUse` matcher `Write|Edit|Bash` → `gate.py`: if
    `.scavenger/active_mission` exists in the project and names a
    mission whose `missions/<name>/GOAL.md` is missing or fails a
    stdlib re-implementation of the never-assumed checks, deny the tool
    call with the reason. Writes to the GOAL.md itself are always
    allowed.
  - `Stop` → `evidence.py`: if the last assistant message claims an
    outcome (regex list in the script: done, sent, merged, paid,
    settled, submitted, and Indonesian equivalents selesai, terkirim,
    sudah dibayar) and contains no evidence reference (URL, outbox id,
    proof check id, or file path), block the stop once with a reason
    asking for the evidence. Do not block twice in a row (check
    `stop_hook_active`).
- Tests in `plugin/tests/` feeding JSON fixtures to each script via
  stdin and asserting stdout/exit code.

### Phase 8 — release readiness

**Task 8.1 — coverage gate**
- Raise CI `--cov-fail-under` to 80. Add tests until it passes; do not
  exclude modules to reach it except `cli.py` argparse wiring and
  platform-specific branches in `notifiers/desktop.py`.

**Task 8.2 — documentation**
- `README.md`: what it does (one paragraph), what it never does
  (sends without approval, guarantees income, spends past budget),
  install, quickstart with the demo mission and fake mode, configuration
  reference link, supervisor setup link, safety model (approval gate,
  verifier fetches its own evidence, brakes), limitations.
- `CONTRIBUTING.md`: setup, test commands, conventions link, how to add
  a channel/adapter/notifier/sender.
- `docs/authoring-channels.md`, `docs/authoring-adapters.md`,
  `docs/authoring-notifiers.md`: protocol, a minimal example, required
  tests (including outward isolation).

**Task 8.3 — fake mode**
- `scavenger run --fake`: uses FakeLLM, a demo channel, and fake
  adapters so a new user sees a full mission run in under a minute with
  no credentials. Reuses the integration test fixtures.

**Task 8.4 — read-only dry run**
- `scavenger run --dry-run`: real adapters and channels, all senders
  replaced by a recorder that writes what it would send to
  `missions/<name>/dry-run.log`. Run it once against live GitHub and a
  test inbox; record the result in the PR description of the release,
  not in the repo.

**Task 8.5 — pre-publication scan**
- Scan the full git history (`git log -p --all`) for secrets (gitleaks
  if available, else regexes for common key formats), email addresses
  other than placeholders, local absolute paths, and personal names.
  Fix by rewriting only if the repo has never been pushed; otherwise
  stop and report.

---

## 4. Definition of done (v0.1)

- All tasks complete with their tests.
- CI green on Linux and Windows, Python 3.11 and 3.12, coverage >= 80%.
- The three integration tests in 5.4 pass.
- Security tests pass: forged approval, edited draft after approval,
  expired token, non-allowlisted Telegram sender, outward isolation.
- `scavenger run --fake` completes a mission end to end.
- `docs/channels/LIVENESS.md` is dated within 7 days of release.
- No secrets, personal data, or local paths in git history.
