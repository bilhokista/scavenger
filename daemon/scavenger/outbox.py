import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx

from scavenger.notifiers import Notice, fan_out

_BODY_MARKER = "\n--- body ---\n"


class OutboxError(Exception):
    pass


class OutboxRefused(Exception):
    pass


@dataclass(frozen=True)
class LimitViolation:
    rule: str
    detail: str


@dataclass(frozen=True)
class DraftSpec:
    kind: str
    target: str
    body: str
    links_json: str = "[]"
    payload_json: str = "{}"
    cost: Decimal = Decimal(0)
    guarantor: str = "none"
    guarantor_evidence: str | None = None


def _normalize_body(body: str) -> str:
    return " ".join(body.split()).lower()


def _check_guarantor(spec) -> list:
    if not spec.guarantor_evidence:
        return [
            LimitViolation("guarantor_evidence", "guarantor set but no evidence URL")
        ]
    try:
        response = httpx.get(spec.guarantor_evidence, timeout=20)
    except httpx.HTTPError as error:
        return [LimitViolation("guarantor_evidence", f"evidence unreachable: {error}")]
    if response.status_code != 200:
        return [
            LimitViolation(
                "guarantor_evidence",
                f"evidence http {response.status_code}",
            )
        ]
    return []


def check_limits(spec, store, github=None, outbox_config=None) -> list:
    # Decision: plan fixes the signature as (spec, store, github).
    # Thresholds live in config; production passes outbox_config, tests
    # rely on the plan defaults below.
    max_prs = outbox_config.max_open_prs_per_repo if outbox_config else 3
    max_sends = outbox_config.max_sends_per_day if outbox_config else 10
    dup_days = outbox_config.duplicate_body_window_days if outbox_config else 7
    violations = []
    if spec.kind == "github_pr" and github is not None:
        try:
            repo = json.loads(spec.payload_json).get("repo", spec.target)
        except ValueError:
            repo = spec.target
        try:
            open_count = github.count_open_prs(repo, github.username)
        except Exception as error:  # noqa: BLE001 - uncountable means refuse
            return [
                LimitViolation(
                    "max_open_prs",
                    f"cannot count open PRs in {repo}: {error}",
                )
            ]
        if open_count >= max_prs:
            violations.append(
                LimitViolation(
                    "max_open_prs",
                    f"{open_count} open PRs by {github.username} in {repo}",
                )
            )
    if spec.guarantor != "none":
        violations.extend(_check_guarantor(spec))
    now = datetime.now(UTC)
    if store.sends_since((now - timedelta(hours=24)).isoformat()) >= max_sends:
        violations.append(
            LimitViolation("max_sends_per_day", f"{max_sends} sends in the last 24h")
        )
    window_start = (now - timedelta(days=dup_days)).isoformat()
    digest = hashlib.sha256(_normalize_body(spec.body).encode("utf-8")).hexdigest()
    for item in store.list_outbox("sent"):
        if item.sent_at is None or item.sent_at < window_start:
            continue
        if item.target == spec.target:
            continue
        if (
            hashlib.sha256(_normalize_body(item.body).encode("utf-8")).hexdigest()
            == digest
        ):
            violations.append(
                LimitViolation(
                    "duplicate_body",
                    f"same body already sent to {item.target}",
                )
            )
            break
    return violations


def canonical_hash(
    kind: str, target: str, body: str, links_json: str, payload_json: str, cost: str
) -> str:
    # Mirrors store.add_outbox so CLI approval can recompute it from the
    # draft file. Change both together.
    canonical = json.dumps(
        {
            "kind": kind,
            "target": target,
            "body": body,
            "links": links_json,
            "payload": payload_json,
            "cost": cost,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _draft_text(
    item_id: str,
    kind: str,
    target: str,
    cost: str,
    links_json: str,
    payload_json: str,
    expires_at: str,
    body: str,
) -> str:
    head = "\n".join(
        [
            f"# Outbox draft {item_id}",
            "",
            f"kind: {kind}",
            f"target: {target}",
            f"cost: {cost}",
            f"links_json: {links_json}",
            f"payload_json: {payload_json}",
            f"expires: {expires_at}",
        ]
    )
    return head + _BODY_MARKER + body


def _parse_draft(text: str) -> dict:
    head, _, body = text.partition(_BODY_MARKER)
    if not _:
        raise OutboxRefused("draft file is not a scavenger draft")
    fields = {}
    for line in head.splitlines():
        if line.startswith("#") or not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    fields["body"] = body
    return fields


class Outbox:
    def __init__(self, store, config, notifiers, clock, github=None) -> None:
        secret = os.environ.get(config.outbox.secret_env, "")
        if not secret:
            raise OutboxError(
                f"missing approval secret: {config.outbox.secret_env} unset"
            )
        if len(secret.encode("utf-8")) < 32:
            raise OutboxError("approval secret must be at least 32 bytes")
        self._secret = secret.encode("utf-8")
        self._store = store
        self._config = config
        self._notifiers = list(notifiers)
        self._clock = clock
        self._github = github

    def create_draft(self, spec: DraftSpec, mission: str, strategy_id: int | None):
        violations = check_limits(spec, self._store, self._github, self._config.outbox)
        expires_at = (
            self._clock.now() + timedelta(hours=self._config.outbox.token_ttl_hours)
        ).isoformat()
        item_id = self._store.add_outbox(
            mission=mission,
            strategy_id=strategy_id,
            kind=spec.kind,
            target=spec.target,
            body=spec.body,
            links_json=spec.links_json,
            payload_json=spec.payload_json,
            cost=spec.cost,
            expires_at=expires_at,
            created_at=self._clock.now().isoformat(),
        )
        item = self._store.get_outbox(item_id)
        draft_dir = self._draft_dir(item.mission)
        draft_dir.mkdir(parents=True, exist_ok=True)
        (draft_dir / f"{item_id}.md").write_text(
            _draft_text(
                item_id,
                spec.kind,
                spec.target,
                str(spec.cost),
                spec.links_json,
                spec.payload_json,
                expires_at,
                spec.body,
            ),
            encoding="utf-8",
        )
        if violations:
            self._store.set_outbox_status(
                item_id,
                "rejected",
                decided_at=self._clock.now().isoformat(),
                decided_via="limit",
            )
            for violation in violations:
                self._store.add_event(
                    mission=mission,
                    level="warn",
                    kind="limit",
                    message=f"{violation.rule}: {violation.detail}",
                )
            return self._store.get_outbox(item_id)
        self._send_approval_request(item)
        return item

    def _draft_dir(self, mission: str):
        return self._config.paths.missions / mission / "outbox"

    def _send_approval_request(self, item) -> None:
        notice = Notice(
            kind="approval_request",
            title=f"approve {item.kind} to {item.target}",
            body=f"{item.body}\n\nReply with:"
            f" /approve {item.id} {self.token_for(item)}",
            outbox_id=item.id,
            token=self.token_for(item),
        )
        fan_out(notice, self._notifiers, store=self._store, mission=item.mission)

    def token_for(self, item) -> str:
        message = f"{item.id}|{item.content_sha256}|{item.expires_at}".encode()
        digest = hmac.new(self._secret, message, "sha256").digest()
        return base64.b32encode(digest)[:20].decode()

    def verify_token(self, item, token: str) -> bool:
        if item.expires_at < self._clock.now().isoformat():
            return False
        current = canonical_hash(
            item.kind,
            item.target,
            item.body,
            item.links_json,
            item.payload_json,
            str(item.cost),
        )
        if not hmac.compare_digest(current, item.content_sha256):
            return False
        expected = self.token_for(item)
        return hmac.compare_digest(expected, token)

    def approve(self, item_id: str, token: str | None, via: str, by: str) -> None:
        item = self._store.get_outbox(item_id)
        if item is None:
            raise OutboxRefused(f"unknown draft: {item_id}")
        if item.status != "awaiting":
            raise OutboxRefused(f"draft {item_id} already {item.status}")
        if token is None:
            if via != "cli":
                raise OutboxRefused("token required outside local approval")
            self._verify_file(item)
        elif not self.verify_token(item, token):
            raise OutboxRefused(f"bad token for draft {item_id}")
        self._store.set_outbox_status(
            item_id,
            "approved",
            decided_at=self._clock.now().isoformat(),
            decided_via=via,
            decided_by=by,
        )

    def reject(self, item_id: str, via: str, by: str) -> None:
        item = self._store.get_outbox(item_id)
        if item is None:
            raise OutboxRefused(f"unknown draft: {item_id}")
        if item.status != "awaiting":
            raise OutboxRefused(f"draft {item_id} already {item.status}")
        self._store.set_outbox_status(
            item_id,
            "rejected",
            decided_at=self._clock.now().isoformat(),
            decided_via=via,
            decided_by=by,
        )

    def _verify_file(self, item) -> None:
        path = self._draft_dir(item.mission) / f"{item.id}.md"
        try:
            fields = _parse_draft(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise OutboxRefused(f"draft file unreadable: {error}") from error
        digest = canonical_hash(
            fields.get("kind", ""),
            fields.get("target", ""),
            fields.get("body", ""),
            fields.get("links_json", ""),
            fields.get("payload_json", ""),
            fields.get("cost", ""),
        )
        if not hmac.compare_digest(digest, item.content_sha256):
            raise OutboxRefused(
                f"draft {item.id} changed on disk; run scavenger redraft {item.id}"
            )

    def redraft(self, item_id: str):
        item = self._store.get_outbox(item_id)
        if item is None:
            raise OutboxRefused(f"unknown draft: {item_id}")
        path = self._draft_dir(item.mission) / f"{item.id}.md"
        fields = _parse_draft(path.read_text(encoding="utf-8"))
        spec = DraftSpec(
            kind=fields.get("kind", item.kind),
            target=fields.get("target", item.target),
            body=fields.get("body", item.body),
            links_json=fields.get("links_json", item.links_json),
            payload_json=fields.get("payload_json", item.payload_json),
            cost=Decimal(fields.get("cost", item.cost)),
        )
        self._store.set_outbox_status(
            item_id,
            "expired",
            decided_at=self._clock.now().isoformat(),
            decided_via="redraft",
        )
        return self.create_draft(spec, item.mission, item.strategy_id)

    def expire_old(self) -> int:
        now_iso = self._clock.now().isoformat()
        count = 0
        for item in self._store.list_outbox("awaiting"):
            if item.expires_at < now_iso:
                self._store.set_outbox_status(
                    item.id,
                    "expired",
                    decided_at=now_iso,
                    decided_via="expiry",
                )
                count += 1
        return count


def create_outbox(store, config, notifiers, clock, github=None) -> Outbox:
    return Outbox(store, config, notifiers, clock, github)
