import sys

import httpx
import respx
from scavenger.notifiers import Notice, NotifyError
from scavenger.notifiers.desktop import DesktopNotifier
from scavenger.notifiers.email import EmailNotifier, SmtpClient
from scavenger.notifiers.ntfy import NtfyNotifier
from scavenger.notifiers.slack import SlackNotifier
from scavenger.notifiers.telegram import TelegramNotifier
from scavenger.store import Store


def make_notice(**overrides):
    fields = {
        "kind": "approval_request",
        "title": "approve this",
        "body": "please review",
    }
    fields.update(overrides)
    return Notice(**fields)


def make_store(tmp_path):
    return Store.open(tmp_path / "ledger.sqlite3", run_dir=tmp_path / "run")


def test_desktop_unsupported_platform_no_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "plan9")
    DesktopNotifier().notify(make_notice())


def test_desktop_missing_binary_warns_no_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: None)
    store = make_store(tmp_path)
    DesktopNotifier(store=store).notify(make_notice())
    events = store._conn.execute("SELECT * FROM events").fetchall()
    assert len(events) == 1
    assert events[0]["level"] == "warn"


class FakeSmtp:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((None, to, subject, body))


def test_email_sends_via_smtp_client():
    smtp = FakeSmtp()
    notifier = EmailNotifier(smtp, to="boss@example.com")
    notifier.notify(make_notice())
    assert smtp.sent[0][1] == "boss@example.com"
    assert "approve this" in smtp.sent[0][2]


def test_smtp_client_class_exists():
    assert SmtpClient is not None


@respx.mock
def test_telegram_send(respx_mock):
    respx_mock.post("https://api.telegram.org/bottoken/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    notifier = TelegramNotifier(
        bot_token="token",
        chat_id="1",
        allowed_user_ids=(7,),
        run_dir="/tmp/nowhere",
    )
    notifier.notify(make_notice())


@respx.mock
def test_telegram_send_failure_raises(respx_mock):
    respx_mock.post("https://api.telegram.org/bottoken/sendMessage").mock(
        return_value=httpx.Response(500, json={})
    )
    notifier = TelegramNotifier(
        bot_token="token",
        chat_id="1",
        allowed_user_ids=(7,),
        run_dir="/tmp/nowhere",
    )
    try:
        notifier.notify(make_notice())
    except NotifyError:
        return
    raise AssertionError("expected NotifyError")


@respx.mock
def test_reply_from_unlisted_user_ignored(tmp_path, respx_mock):
    respx_mock.get("https://api.telegram.org/bottoken/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 5,
                        "message": {
                            "from": {"id": 999},
                            "chat": {"id": 1},
                            "text": "/approve abc123 ABCD",
                            "date": 1,
                        },
                    },
                ],
            },
        )
    )
    notifier = TelegramNotifier(
        bot_token="token",
        chat_id="1",
        allowed_user_ids=(7,),
        run_dir=str(tmp_path),
    )
    assert notifier.poll_replies() == []


@respx.mock
def test_malformed_command_ignored(tmp_path, respx_mock):
    respx_mock.get("https://api.telegram.org/bottoken/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 6,
                        "message": {
                            "from": {"id": 7},
                            "chat": {"id": 1},
                            "text": "hello there",
                            "date": 1,
                        },
                    },
                ],
            },
        )
    )
    notifier = TelegramNotifier(
        bot_token="token",
        chat_id="1",
        allowed_user_ids=(7,),
        run_dir=str(tmp_path),
    )
    assert notifier.poll_replies() == []


@respx.mock
def test_offset_persisted(tmp_path, respx_mock):
    token32 = "a" * 32
    respx_mock.get("https://api.telegram.org/bottoken/getUpdates").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 8,
                        "message": {
                            "from": {"id": 7},
                            "chat": {"id": 1},
                            "text": f"/approve {token32} ABCDEFGHIJKLMNOPQRST",
                            "date": 1,
                        },
                    },
                ],
            },
        )
    )
    notifier = TelegramNotifier(
        bot_token="token",
        chat_id="1",
        allowed_user_ids=(7,),
        run_dir=str(tmp_path),
    )
    replies = notifier.poll_replies()
    assert len(replies) == 1
    assert replies[0].outbox_id == token32
    offset_file = tmp_path / "telegram_offset"
    assert offset_file.read_text(encoding="utf-8").strip() == "9"


@respx.mock
def test_discord_posts(respx_mock):
    respx_mock.post("https://discord.example/hook").mock(
        return_value=httpx.Response(204)
    )
    from scavenger.notifiers.discord import DiscordNotifier as Discord

    Discord("https://discord.example/hook").notify(make_notice())


@respx.mock
def test_slack_failure_raises(respx_mock):
    respx_mock.post("https://hooks.slack.example/hook").mock(
        return_value=httpx.Response(500, json={})
    )
    try:
        SlackNotifier("https://hooks.slack.example/hook").notify(make_notice())
    except NotifyError:
        return
    raise AssertionError("expected NotifyError")


@respx.mock
def test_ntfy_posts(respx_mock):
    respx_mock.post("https://ntfy.sh/topic1").mock(
        return_value=httpx.Response(200, json={})
    )
    NtfyNotifier("https://ntfy.sh", "topic1").notify(make_notice())
