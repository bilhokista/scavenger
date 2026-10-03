import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from scavenger.clock import now

_MIGRATIONS = [
    """
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
  path                TEXT NOT NULL,
  path_key            TEXT NOT NULL,
  outward_key         TEXT NOT NULL,
  payout_amount       TEXT NOT NULL,
  payout_currency     TEXT NOT NULL,
  guarantor           TEXT NOT NULL CHECK (guarantor IN
                       ('escrow','grant','signed_client','none')),
  guarantor_evidence  TEXT,
  capital_needed      TEXT NOT NULL,
  hours_first_proof   REAL NOT NULL,
  hours_settlement    REAL NOT NULL,
  probability         REAL NOT NULL,
  probability_source  TEXT NOT NULL CHECK (probability_source IN ('ledger','estimate')),
  probability_note    TEXT NOT NULL,
  score               TEXT NOT NULL,
  rungs_json          TEXT NOT NULL,
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
  locator_json     TEXT,
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
  evidence_raw    TEXT NOT NULL,
  evidence_sha256 TEXT NOT NULL,
  error           TEXT,
  checked_at      TEXT NOT NULL
);

CREATE TABLE outbox (
  id              TEXT PRIMARY KEY,
  mission         TEXT NOT NULL REFERENCES missions(name),
  strategy_id     INTEGER REFERENCES strategies(id),
  kind            TEXT NOT NULL CHECK (kind IN
                   ('email','github_pr','github_comment','manual','strategy_start')),
  target          TEXT NOT NULL,
  body            TEXT NOT NULL,
  links_json      TEXT NOT NULL DEFAULT '[]',
  payload_json    TEXT NOT NULL DEFAULT '{}',
  cost            TEXT NOT NULL DEFAULT '0',
  content_sha256  TEXT NOT NULL,
  expires_at      TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN
                   ('awaiting','approved','rejected','expired','sent','failed')),
  decided_at      TEXT,
  decided_via     TEXT,
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
  source      TEXT NOT NULL
);

CREATE TABLE settlements (
  id              INTEGER PRIMARY KEY,
  mission         TEXT NOT NULL REFERENCES missions(name),
  strategy_id     INTEGER REFERENCES strategies(id),
  amount          TEXT NOT NULL,
  currency        TEXT NOT NULL,
  amount_in_target TEXT,
  message_id      TEXT NOT NULL UNIQUE,
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
  hours_to_rung_json TEXT NOT NULL,
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
"""
]


@dataclass(frozen=True)
class Mission:
    name: str
    goal_path: str
    goal_sha256: str
    status: str
    target_amount: Decimal
    target_currency: str
    deadline: str
    empty_refills: int
    last_research_at: str | None
    created_at: str
    stopped_at: str | None
    stop_reason: str | None


@dataclass(frozen=True)
class Strategy:
    id: int
    mission: str
    channel: str
    path: str
    path_key: str
    outward_key: str
    payout_amount: Decimal
    payout_currency: str
    guarantor: str
    guarantor_evidence: str | None
    capital_needed: Decimal
    hours_first_proof: float
    hours_settlement: float
    probability: float
    probability_source: str
    probability_note: str
    score: Decimal
    rungs_json: str
    status: str
    rung_index: int
    failed_rounds: int
    loss: Decimal
    pending_since: str | None
    unverified_streak: int
    next_check_at: str | None
    created_at: str
    closed_at: str | None
    death_cause: str | None
    death_evidence: int | None


@dataclass(frozen=True)
class Round:
    id: int
    strategy_id: int
    started_at: str
    ended_at: str | None
    action_kind: str
    action_fingerprint: str
    outbox_id: str | None
    locator_json: str | None
    result_state: str | None


@dataclass(frozen=True)
class ProofCheck:
    id: int
    strategy_id: int
    round_id: int | None
    rung: str
    adapter: str
    locator_json: str
    state: str
    evidence_raw: str
    evidence_sha256: str
    error: str | None
    checked_at: str


@dataclass(frozen=True)
class OutboxItem:
    id: str
    mission: str
    strategy_id: int | None
    kind: str
    target: str
    body: str
    links_json: str
    payload_json: str
    cost: Decimal
    content_sha256: str
    expires_at: str
    status: str
    decided_at: str | None
    decided_via: str | None
    decided_by: str | None
    sent_at: str | None
    send_result_json: str | None
    created_at: str


@dataclass(frozen=True)
class SpendTotal:
    money: Decimal
    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class LedgerRow:
    id: int
    mission: str
    strategy_id: int
    channel: str
    path_key: str
    outcome: str
    death_cause: str | None
    rung_reached: str
    failed_rounds: int
    money_spent: Decimal
    tokens: int
    hours_to_rung_json: str
    closed_at: str


@dataclass(frozen=True)
class ChannelBlock:
    channel: str
    cause: str
    until: str
    created_at: str


@dataclass(frozen=True)
class SettlementRow:
    amount: Decimal
    currency: str
    message_id: str
    at: str


def _mission_row(row: sqlite3.Row) -> Mission:
    return Mission(
        name=row["name"],
        goal_path=row["goal_path"],
        goal_sha256=row["goal_sha256"],
        status=row["status"],
        target_amount=Decimal(row["target_amount"]),
        target_currency=row["target_currency"],
        deadline=row["deadline"],
        empty_refills=row["empty_refills"],
        last_research_at=row["last_research_at"],
        created_at=row["created_at"],
        stopped_at=row["stopped_at"],
        stop_reason=row["stop_reason"],
    )


def _strategy_row(row: sqlite3.Row) -> Strategy:
    return Strategy(
        id=row["id"],
        mission=row["mission"],
        channel=row["channel"],
        path=row["path"],
        path_key=row["path_key"],
        outward_key=row["outward_key"],
        payout_amount=Decimal(row["payout_amount"]),
        payout_currency=row["payout_currency"],
        guarantor=row["guarantor"],
        guarantor_evidence=row["guarantor_evidence"],
        capital_needed=Decimal(row["capital_needed"]),
        hours_first_proof=row["hours_first_proof"],
        hours_settlement=row["hours_settlement"],
        probability=row["probability"],
        probability_source=row["probability_source"],
        probability_note=row["probability_note"],
        score=Decimal(row["score"]),
        rungs_json=row["rungs_json"],
        status=row["status"],
        rung_index=row["rung_index"],
        failed_rounds=row["failed_rounds"],
        loss=Decimal(row["loss"]),
        pending_since=row["pending_since"],
        unverified_streak=row["unverified_streak"],
        next_check_at=row["next_check_at"],
        created_at=row["created_at"],
        closed_at=row["closed_at"],
        death_cause=row["death_cause"],
        death_evidence=row["death_evidence"],
    )


class Store:
    def __init__(self, conn: sqlite3.Connection, run_dir: Path) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row
        self._run_dir = run_dir

    @classmethod
    def open(cls, path: str | Path, run_dir: str | Path | None = None) -> "Store":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        base = Path(run_dir) if run_dir else path.parent / ".scavenger"
        store = cls(conn, base)
        store._migrate()
        return store

    def close(self) -> None:
        self._conn.close()

    def tx(self) -> sqlite3.Connection:
        return self._conn

    def _migrate(self) -> None:
        with self._conn:
            try:
                row = self._conn.execute(
                    "SELECT version FROM schema_version"
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
            current = row[0] if row else 0
            for index, sql in enumerate(_MIGRATIONS, start=1):
                if index > current:
                    self._conn.executescript(sql)
                    self._conn.execute(
                        "INSERT INTO schema_version (version) VALUES (?)", (index,)
                    )

    def create_mission(
        self,
        *,
        name: str,
        goal_path: str,
        goal_sha256: str,
        status: str,
        target_amount: Decimal,
        target_currency: str,
        deadline: str,
        created_at: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO missions (name, goal_path, goal_sha256, status,"
                " target_amount, target_currency, deadline, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    name,
                    goal_path,
                    goal_sha256,
                    status,
                    str(target_amount),
                    target_currency,
                    deadline,
                    created_at,
                ),
            )

    def get_mission(self, name: str) -> Mission | None:
        row = self._conn.execute(
            "SELECT * FROM missions WHERE name = ?", (name,)
        ).fetchone()
        return _mission_row(row) if row else None

    def set_mission_status(
        self,
        name: str,
        status: str,
        *,
        stopped_at: str | None = None,
        stop_reason: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE missions SET status = ?, stopped_at = ?,"
                " stop_reason = ? WHERE name = ?",
                (status, stopped_at, stop_reason, name),
            )

    def add_strategy(
        self,
        *,
        mission: str,
        channel: str,
        path: str,
        path_key: str,
        outward_key: str,
        payout_amount: Decimal,
        payout_currency: str,
        guarantor: str,
        guarantor_evidence: str | None,
        capital_needed: Decimal,
        hours_first_proof: float,
        hours_settlement: float,
        probability: float,
        probability_source: str,
        probability_note: str,
        score: Decimal,
        rungs_json: str,
        status: str = "queued",
        created_at: str,
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO strategies (mission, channel, path, path_key,"
                " outward_key, payout_amount, payout_currency, guarantor,"
                " guarantor_evidence, capital_needed, hours_first_proof,"
                " hours_settlement, probability, probability_source,"
                " probability_note, score, rungs_json, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mission,
                    channel,
                    path,
                    path_key,
                    outward_key,
                    str(payout_amount),
                    payout_currency,
                    guarantor,
                    guarantor_evidence,
                    str(capital_needed),
                    hours_first_proof,
                    hours_settlement,
                    probability,
                    probability_source,
                    probability_note,
                    str(score),
                    rungs_json,
                    status,
                    created_at,
                ),
            )
            return cursor.lastrowid

    def get_strategy(self, strategy_id: int) -> Strategy | None:
        row = self._conn.execute(
            "SELECT * FROM strategies WHERE id = ?", (strategy_id,)
        ).fetchone()
        return _strategy_row(row) if row else None

    def list_strategies(
        self, mission: str, statuses: tuple | list | None = None
    ) -> list:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = self._conn.execute(
                f"SELECT * FROM strategies WHERE mission = ? AND status IN"
                f" ({placeholders}) ORDER BY id",
                (mission, *statuses),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM strategies WHERE mission = ? ORDER BY id",
                (mission,),
            ).fetchall()
        return [_strategy_row(row) for row in rows]

    _STRATEGY_FIELDS = frozenset(
        {
            "channel",
            "path",
            "path_key",
            "outward_key",
            "payout_amount",
            "payout_currency",
            "guarantor",
            "guarantor_evidence",
            "capital_needed",
            "hours_first_proof",
            "hours_settlement",
            "probability",
            "probability_source",
            "probability_note",
            "score",
            "rungs_json",
            "status",
            "rung_index",
            "failed_rounds",
            "loss",
            "pending_since",
            "unverified_streak",
            "next_check_at",
            "closed_at",
            "death_cause",
            "death_evidence",
        }
    )

    def update_strategy(self, strategy_id: int, **fields) -> None:
        unknown = set(fields) - self._STRATEGY_FIELDS
        if unknown:
            raise ValueError(f"unknown strategy fields: {sorted(unknown)}")
        if not fields:
            return
        values = [
            str(value) if isinstance(value, Decimal) else value
            for value in fields.values()
        ]
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._conn:
            self._conn.execute(
                f"UPDATE strategies SET {assignments} WHERE id = ?",
                (*values, strategy_id),
            )

    def start_round(
        self,
        strategy_id: int,
        *,
        started_at: str,
        action_kind: str,
        action_fingerprint: str,
        outbox_id: str | None = None,
        locator_json: str | None = None,
    ) -> int:
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO rounds (strategy_id, started_at, action_kind,"
                " action_fingerprint, outbox_id, locator_json)"
                " VALUES (?,?,?,?,?,?)",
                (
                    strategy_id,
                    started_at,
                    action_kind,
                    action_fingerprint,
                    outbox_id,
                    locator_json,
                ),
            )
            return cursor.lastrowid

    def end_round(self, round_id: int, *, ended_at: str, result_state: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE rounds SET ended_at = ?, result_state = ? WHERE id = ?",
                (ended_at, result_state, round_id),
            )

    def latest_round(self, strategy_id: int) -> Round | None:
        row = self._conn.execute(
            "SELECT * FROM rounds WHERE strategy_id = ? ORDER BY id DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()
        if not row:
            return None
        return Round(
            id=row["id"],
            strategy_id=row["strategy_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            action_kind=row["action_kind"],
            action_fingerprint=row["action_fingerprint"],
            outbox_id=row["outbox_id"],
            locator_json=row["locator_json"],
            result_state=row["result_state"],
        )

    def strategy_spend(self, strategy_id: int) -> SpendTotal:
        row = self._conn.execute(
            "SELECT SUM(money) AS money, SUM(tokens_in) AS tokens_in,"
            " SUM(tokens_out) AS tokens_out FROM spend WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
        money = row["money"]
        return SpendTotal(
            money=Decimal(str(money)) if money is not None else Decimal(0),
            tokens_in=row["tokens_in"] or 0,
            tokens_out=row["tokens_out"] or 0,
        )

    def add_proof_check(
        self,
        strategy_id: int,
        round_id: int | None,
        rung: str,
        adapter: str,
        locator_json: str,
        state: str,
        evidence_raw: str,
        error: str | None,
        checked_at: str,
    ) -> int:
        raw = evidence_raw[:65536]
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self._conn:
            cursor = self._conn.execute(
                "INSERT INTO proof_checks (strategy_id, round_id, rung,"
                " adapter, locator_json, state, evidence_raw,"
                " evidence_sha256, error, checked_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    strategy_id,
                    round_id,
                    rung,
                    adapter,
                    locator_json,
                    state,
                    raw,
                    digest,
                    error,
                    checked_at,
                ),
            )
            return cursor.lastrowid

    def last_proof_checks(self, strategy_id: int, n: int) -> list:
        rows = self._conn.execute(
            "SELECT * FROM proof_checks WHERE strategy_id = ? ORDER BY id DESC LIMIT ?",
            (strategy_id, n),
        ).fetchall()
        return [
            ProofCheck(
                id=row["id"],
                strategy_id=row["strategy_id"],
                round_id=row["round_id"],
                rung=row["rung"],
                adapter=row["adapter"],
                locator_json=row["locator_json"],
                state=row["state"],
                evidence_raw=row["evidence_raw"],
                evidence_sha256=row["evidence_sha256"],
                error=row["error"],
                checked_at=row["checked_at"],
            )
            for row in rows
        ]

    def add_spend(
        self,
        *,
        mission: str,
        strategy_id: int | None,
        at: str,
        tokens_in: int,
        tokens_out: int,
        money: Decimal,
        source: str,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO spend (mission, strategy_id, at, tokens_in,"
                " tokens_out, money, source) VALUES (?,?,?,?,?,?,?)",
                (mission, strategy_id, at, tokens_in, tokens_out, str(money), source),
            )

    def spend_since(self, mission: str | None, since: str) -> SpendTotal:
        if mission is None:
            rows = self._conn.execute(
                "SELECT money, tokens_in, tokens_out FROM spend WHERE at >= ?",
                (since,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT money, tokens_in, tokens_out FROM spend"
                " WHERE mission = ? AND at >= ?",
                (mission, since),
            ).fetchall()
        return SpendTotal(
            money=sum((Decimal(row["money"]) for row in rows), Decimal(0)),
            tokens_in=sum(row["tokens_in"] for row in rows),
            tokens_out=sum(row["tokens_out"] for row in rows),
        )

    def add_settlement(
        self,
        *,
        mission: str,
        strategy_id: int | None,
        amount: Decimal,
        currency: str,
        amount_in_target: Decimal | None,
        message_id: str,
        proof_check_id: int,
        at: str,
    ) -> bool:
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO settlements (mission, strategy_id, amount,"
                    " currency, amount_in_target, message_id,"
                    " proof_check_id, at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        mission,
                        strategy_id,
                        str(amount),
                        currency,
                        str(amount_in_target) if amount_in_target is not None else None,
                        message_id,
                        proof_check_id,
                        at,
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def settled_in_target(self, mission: str) -> Decimal:
        rows = self._conn.execute(
            "SELECT amount_in_target FROM settlements"
            " WHERE mission = ? AND amount_in_target IS NOT NULL",
            (mission,),
        ).fetchall()
        return sum((Decimal(row["amount_in_target"]) for row in rows), Decimal(0))

    def add_outbox(
        self,
        *,
        mission: str,
        strategy_id: int | None,
        kind: str,
        target: str,
        body: str,
        links_json: str = "[]",
        payload_json: str = "{}",
        cost: Decimal = Decimal(0),
        content_sha256: str | None = None,
        expires_at: str,
        status: str = "awaiting",
        created_at: str,
        id: str | None = None,
    ) -> str:
        item_id = id or uuid.uuid4().hex
        if content_sha256 is None:
            canonical = json.dumps(
                {
                    "kind": kind,
                    "target": target,
                    "body": body,
                    "links": links_json,
                    "payload": payload_json,
                    "cost": str(cost),
                },
                sort_keys=True,
            )
            content_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._conn:
            self._conn.execute(
                "INSERT INTO outbox (id, mission, strategy_id, kind, target,"
                " body, links_json, payload_json, cost, content_sha256,"
                " expires_at, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    mission,
                    strategy_id,
                    kind,
                    target,
                    body,
                    links_json,
                    payload_json,
                    str(cost),
                    content_sha256,
                    expires_at,
                    status,
                    created_at,
                ),
            )
        return item_id

    def get_outbox(self, item_id: str) -> OutboxItem | None:
        row = self._conn.execute(
            "SELECT * FROM outbox WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return None
        return OutboxItem(
            id=row["id"],
            mission=row["mission"],
            strategy_id=row["strategy_id"],
            kind=row["kind"],
            target=row["target"],
            body=row["body"],
            links_json=row["links_json"],
            payload_json=row["payload_json"],
            cost=Decimal(row["cost"]),
            content_sha256=row["content_sha256"],
            expires_at=row["expires_at"],
            status=row["status"],
            decided_at=row["decided_at"],
            decided_via=row["decided_via"],
            decided_by=row["decided_by"],
            sent_at=row["sent_at"],
            send_result_json=row["send_result_json"],
            created_at=row["created_at"],
        )

    def set_outbox_status(self, item_id: str, status: str, **fields) -> None:
        allowed = {
            "decided_at",
            "decided_via",
            "decided_by",
            "sent_at",
            "send_result_json",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown outbox fields: {sorted(unknown)}")
        assignments = ", ".join(["status = ?"] + [f"{k} = ?" for k in fields])
        with self._conn:
            self._conn.execute(
                f"UPDATE outbox SET {assignments} WHERE id = ?",
                (status, *fields.values(), item_id),
            )

    def sends_since(self, since: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM outbox WHERE status = 'sent' AND sent_at >= ?",
            (since,),
        ).fetchone()
        return row["n"]

    def close_strategy(
        self,
        strategy_id: int,
        *,
        status: str,
        death_cause: str | None,
        rung_reached: str,
        failed_rounds: int,
        money_spent: Decimal,
        tokens: int,
        hours_to_rung_json: str,
        closed_at: str,
    ) -> None:
        strategy = self.get_strategy(strategy_id)
        outcome = "won" if status == "won" else "dead"
        with self._conn:
            self._conn.execute(
                "UPDATE strategies SET status = ?, closed_at = ?,"
                " death_cause = ?, failed_rounds = ? WHERE id = ?",
                (status, closed_at, death_cause, failed_rounds, strategy_id),
            )
            self._conn.execute(
                "INSERT INTO ledger (mission, strategy_id, channel,"
                " path_key, outcome, death_cause, rung_reached,"
                " failed_rounds, money_spent, tokens, hours_to_rung_json,"
                " closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    strategy.mission,
                    strategy_id,
                    strategy.channel,
                    strategy.path_key,
                    outcome,
                    death_cause,
                    rung_reached,
                    failed_rounds,
                    str(money_spent),
                    tokens,
                    hours_to_rung_json,
                    closed_at,
                ),
            )

    def ledger_for_channel(self, channel: str) -> list:
        rows = self._conn.execute(
            "SELECT * FROM ledger WHERE channel = ? ORDER BY closed_at",
            (channel,),
        ).fetchall()
        return [
            LedgerRow(
                id=row["id"],
                mission=row["mission"],
                strategy_id=row["strategy_id"],
                channel=row["channel"],
                path_key=row["path_key"],
                outcome=row["outcome"],
                death_cause=row["death_cause"],
                rung_reached=row["rung_reached"],
                failed_rounds=row["failed_rounds"],
                money_spent=Decimal(row["money_spent"]),
                tokens=row["tokens"],
                hours_to_rung_json=row["hours_to_rung_json"],
                closed_at=row["closed_at"],
            )
            for row in rows
        ]

    def block_channel(
        self, channel: str, cause: str, until: str, *, created_at: str | None = None
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO channel_blocks (channel, cause, until,"
                " created_at) VALUES (?,?,?,?)"
                " ON CONFLICT(channel) DO UPDATE SET cause = excluded.cause,"
                " until = excluded.until, created_at = excluded.created_at",
                (channel, cause, until, created_at or now().isoformat()),
            )

    def unblock_channel(self, channel: str) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM channel_blocks WHERE channel = ?", (channel,)
            )

    def list_blocks(self) -> list:
        rows = self._conn.execute(
            "SELECT * FROM channel_blocks ORDER BY channel"
        ).fetchall()
        return [
            ChannelBlock(
                channel=row["channel"],
                cause=row["cause"],
                until=row["until"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_settlements(self, mission: str) -> list:
        rows = self._conn.execute(
            "SELECT amount, currency, message_id, at FROM settlements"
            " WHERE mission = ? ORDER BY id",
            (mission,),
        ).fetchall()
        return [
            SettlementRow(
                amount=Decimal(row["amount"]),
                currency=row["currency"],
                message_id=row["message_id"],
                at=row["at"],
            )
            for row in rows
        ]

    def active_block(self, channel: str) -> ChannelBlock | None:
        row = self._conn.execute(
            "SELECT * FROM channel_blocks WHERE channel = ?", (channel,)
        ).fetchone()
        if not row:
            return None
        if row["until"] < now().isoformat():
            return None
        return ChannelBlock(
            channel=row["channel"],
            cause=row["cause"],
            until=row["until"],
            created_at=row["created_at"],
        )

    def add_event(
        self,
        *,
        mission: str | None,
        level: str,
        kind: str,
        message: str,
        at: str | None = None,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO events (at, mission, level, kind, message)"
                " VALUES (?,?,?,?,?)",
                (at or now().isoformat(), mission, level, kind, message),
            )

    def write_heartbeat(self) -> None:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        (self._run_dir / "heartbeat").write_text(now().isoformat(), encoding="utf-8")

    def read_heartbeat(self) -> str | None:
        path = self._run_dir / "heartbeat"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8").strip()
