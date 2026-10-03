import hmac as hmac_module
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from scavenger.config import load as load_config
from scavenger.outbox import (
    DraftSpec,
    OutboxError,
    OutboxRefused,
    create_outbox,
)
from scavenger.store import Store

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


class FakeClock:
    def now(self):
        return NOW


def setup(tmp_path, monkeypatch, secret=b"x" * 32):
    example = Path(__file__).parents[2] / "scavenger.example.toml"
    conf_path = tmp_path / "scavenger.toml"
    conf_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_config(conf_path)
    monkeypatch.setenv(config.outbox.secret_env, secret.decode())
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


def draft(outbox):
    return outbox.create_draft(
        DraftSpec(kind="email", target="boss@example.com", body="hello"),
        mission="demo",
        strategy_id=None,
    )


def test_token_roundtrip(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    item = draft(outbox)
    assert outbox.verify_token(item, outbox.token_for(item)) is True


def test_token_fails_after_body_change(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    item = draft(outbox)
    token = outbox.token_for(item)
    store._conn.execute("UPDATE outbox SET body = 'tampered' WHERE id = ?", (item.id,))
    changed = store.get_outbox(item.id)
    assert outbox.verify_token(changed, token) is False


def test_token_fails_after_expiry(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    item = draft(outbox)
    token = outbox.token_for(item)
    store._conn.execute(
        "UPDATE outbox SET expires_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (item.id,),
    )
    expired = store.get_outbox(item.id)
    assert outbox.verify_token(expired, token) is False


def test_token_compare_is_constant_time(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    item = draft(outbox)
    token = outbox.token_for(item)
    calls = []
    real = hmac_module.compare_digest

    def spy(first, second):
        calls.append((first, second))
        return real(first, second)

    monkeypatch.setattr(hmac_module, "compare_digest", spy)
    assert outbox.verify_token(item, token) is True
    assert calls


def test_cli_approve_refuses_edited_file(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    (tmp_path / "missions").mkdir()
    outbox = create_outbox(store, config, [], FakeClock())
    item = draft(outbox)
    draft_file = tmp_path / "missions" / "demo" / "outbox" / f"{item.id}.md"
    text = draft_file.read_text(encoding="utf-8")
    draft_file.write_text(text.replace("hello", "HELLO Edited"), encoding="utf-8")
    with pytest.raises(OutboxRefused):
        outbox.approve(item.id, None, via="cli", by="human")


def test_redraft_expires_old(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    (tmp_path / "missions").mkdir()
    outbox = create_outbox(store, config, [], FakeClock())
    item = draft(outbox)
    draft_file = tmp_path / "missions" / "demo" / "outbox" / f"{item.id}.md"
    text = draft_file.read_text(encoding="utf-8")
    draft_file.write_text(text.replace("hello", "hello v2"), encoding="utf-8")
    new_item = outbox.redraft(item.id)
    assert store.get_outbox(item.id).status == "expired"
    assert store.get_outbox(new_item.id).status == "awaiting"
    outbox.approve(new_item.id, None, via="cli", by="human")
    assert store.get_outbox(new_item.id).status == "approved"


def test_missing_secret_fails_startup(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    monkeypatch.delenv(config.outbox.secret_env, raising=False)
    with pytest.raises(OutboxError):
        create_outbox(store, config, [], FakeClock())


def test_secret_shorter_than_32_bytes_fails(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    monkeypatch.setenv(config.outbox.secret_env, "short")
    with pytest.raises(OutboxError):
        create_outbox(store, config, [], FakeClock())
