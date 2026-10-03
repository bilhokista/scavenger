from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import respx
from scavenger.config import load as load_config
from scavenger.outbox import DraftSpec, check_limits, create_outbox
from scavenger.store import Store


class FakeClock:
    def now(self):
        return datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class FakeGithub:
    def __init__(self, open_prs=0, username="bot"):
        self._open_prs = open_prs
        self.username = username

    def count_open_prs(self, repo, author):
        return self._open_prs


def setup(tmp_path, monkeypatch):
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    conf_path = tmp_path / "scavenger.toml"
    conf_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(conf_path)
    monkeypatch.setenv(config.outbox.secret_env, "x" * 32)
    store = Store.open(tmp_path / "ledger.sqlite3", run_dir=tmp_path / "run")
    store.create_mission(
        name="demo",
        goal_path="missions/demo/GOAL.md",
        goal_sha256="abc",
        status="active",
        target_amount=Decimal("1500.00"),
        target_currency="USD",
        deadline="2026-11-30T16:59:59+00:00",
        created_at="2026-10-03T05:00:00+00:00",
    )
    return config, store


def pr_spec():
    return DraftSpec(
        kind="github_pr",
        target="o/r",
        body="fix",
        payload_json='{"repo": "o/r"}',
    )


def mark_sent(store, target, body, at):
    item_id = store.add_outbox(
        mission="demo",
        strategy_id=None,
        kind="email",
        target=target,
        body=body,
        expires_at="2026-12-01T00:00:00+00:00",
        created_at="2026-10-01T00:00:00+00:00",
    )
    store.set_outbox_status(
        item_id,
        "sent",
        sent_at=at,
        decided_via="cli",
        decided_by="human",
        decided_at=at,
    )


def test_open_pr_limit(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(
        store, config, [], FakeClock(), github=FakeGithub(open_prs=3)
    )
    item = outbox.create_draft(pr_spec(), "demo", None)
    assert item.status == "rejected"
    assert item.decided_via == "limit"


def test_open_pr_under_limit_passes(tmp_path, monkeypatch):
    _config, store = setup(tmp_path, monkeypatch)
    violations = check_limits(pr_spec(), store, FakeGithub(open_prs=2))
    assert violations == []


@respx.mock
def test_guarantor_evidence_must_return_200(tmp_path, monkeypatch, respx_mock):
    respx_mock.get("https://grants.example/award/1").mock(
        return_value=httpx.Response(404)
    )
    _config, store = setup(tmp_path, monkeypatch)
    spec = DraftSpec(
        kind="email",
        target="a@b.c",
        body="hi",
        guarantor="grant",
        guarantor_evidence="https://grants.example/award/1",
    )
    violations = check_limits(spec, store, FakeGithub())
    assert len(violations) == 1
    assert violations[0].rule == "guarantor_evidence"


def test_max_sends_per_day(tmp_path, monkeypatch):
    _config, store = setup(tmp_path, monkeypatch)
    now = datetime.now(UTC).isoformat()
    for index in range(10):
        mark_sent(store, f"user{index}@x.y", f"body {index}", now)
    violations = check_limits(
        DraftSpec(kind="email", target="new@x.y", body="fresh body here"),
        store,
        FakeGithub(),
    )
    assert any(v.rule == "max_sends_per_day" for v in violations)


def test_duplicate_body(tmp_path, monkeypatch):
    _config, store = setup(tmp_path, monkeypatch)
    now = datetime.now(UTC).isoformat()
    mark_sent(store, "first@x.y", "Same Offer Text", now)
    violations = check_limits(
        DraftSpec(kind="email", target="second@x.y", body="same offer text"),
        store,
        FakeGithub(),
    )
    assert any(v.rule == "duplicate_body" for v in violations)


def test_limits_rechecked_at_send_time(tmp_path, monkeypatch):
    _config, store = setup(tmp_path, monkeypatch)
    spec = DraftSpec(kind="email", target="new@x.y", body="unique body xyz")
    assert check_limits(spec, store, FakeGithub()) == []
    now = datetime.now(UTC).isoformat()
    for index in range(10):
        mark_sent(store, f"user{index}@x.y", f"other body {index}", now)
    assert any(
        v.rule == "max_sends_per_day" for v in check_limits(spec, store, FakeGithub())
    )
