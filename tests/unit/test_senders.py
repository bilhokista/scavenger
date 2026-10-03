import json
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scavenger.config import load as load_config
from scavenger.outbox import DraftSpec, create_outbox
from scavenger.senders import SendResult, send_approved
from scavenger.senders.email_smtp import EmailSender
from scavenger.senders.github_comment import GithubCommentSender
from scavenger.senders.github_pr import GithubPrSender
from scavenger.senders.manual import ManualSender
from scavenger.store import OutboxItem, Store


class FakeClock:
    def now(self):
        return datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


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


def approve_email(
    store, outbox, target="boss@example.com", body="hello", strategy_id=None
):
    item = outbox.create_draft(
        DraftSpec(kind="email", target=target, body=body),
        "demo",
        strategy_id,
    )
    outbox.approve(item.id, None, via="cli", by="human")
    return item


class FakeSmtp:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body, message_id=None):
        self.sent.append((to, subject, body, message_id))


class FakeGithub:
    def __init__(self, remote, username="bot"):
        self._remote = remote
        self.username = username
        self.prs = []

    def ensure_fork(self, repo):
        return f"{self.username}/{repo.split('/')[-1]}"

    def clone_url(self, full_name):
        return str(self._remote)

    def create_pr(self, repo, title, head, base):
        self.prs.append((repo, title, head, base))
        return 7

    def post_comment(self, issue_url, body):
        self.comments = getattr(self, "comments", [])
        self.comments.append((issue_url, body))
        return {"repo": "o/r", "issue": 3}

    def count_open_prs(self, repo, author):
        return 0


def make_bare_remote(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "-b", "main", str(seed)], check=True, capture_output=True
    )
    (seed / "note.txt").write_text("hello\n", encoding="utf-8", newline="\n")
    subprocess.run(
        ["git", "-C", str(seed), "add", "note.txt"], check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(seed),
            "-c",
            "user.email=t@t.t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "seed",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", str(remote), "main:main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
        check=True,
        capture_output=True,
    )
    return remote


def test_email_sender_returns_message_id():
    smtp = FakeSmtp()
    sender = EmailSender(smtp, "me@example.com")
    item = OutboxItem(
        id="x",
        mission="demo",
        strategy_id=None,
        kind="email",
        target="boss@example.com",
        body="hi",
        links_json="[]",
        payload_json='{"subject": "update"}',
        cost=Decimal(0),
        content_sha256="s",
        expires_at="2030-01-01T00:00:00+00:00",
        status="approved",
        decided_at=None,
        decided_via=None,
        decided_by=None,
        sent_at=None,
        send_result_json=None,
        created_at="2026-10-04T11:00:00+00:00",
    )
    result = sender.send(item)
    assert isinstance(result, SendResult)
    assert result.ok is True
    assert result.locator["counterparty"] == "boss@example.com"
    assert "@" in result.locator["thread_message_id"]
    assert smtp.sent[0][3] == result.locator["thread_message_id"]


def test_github_pr_applies_patch_and_returns_number(tmp_path):
    remote = make_bare_remote(tmp_path)
    patch = tmp_path / "fix.patch"
    patch.write_text(
        "--- a/note.txt\n+++ b/note.txt\n@@ -1 +1,2 @@\n hello\n+world\n",
        encoding="utf-8",
        newline="\n",
    )
    github = FakeGithub(remote)
    sender = GithubPrSender(github, username="bot")
    item = OutboxItem(
        id="y",
        mission="demo",
        strategy_id=None,
        kind="github_pr",
        target="o/r",
        body="fix",
        links_json="[]",
        payload_json=json.dumps(
            {
                "repo": "o/r",
                "base": "main",
                "branch": "scav-fix-1",
                "title": "Fix note",
                "patch_path": str(patch),
            }
        ),
        cost=Decimal(0),
        content_sha256="s",
        expires_at="2030-01-01T00:00:00+00:00",
        status="approved",
        decided_at=None,
        decided_via=None,
        decided_by=None,
        sent_at=None,
        send_result_json=None,
        created_at="2026-10-04T11:00:00+00:00",
    )
    result = sender.send(item)
    assert result.ok is True
    assert result.locator == {"repo": "o/r", "pr": 7}
    check = subprocess.run(
        ["git", "--git-dir", str(remote), "show", "scav-fix-1:note.txt"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert check.stdout == "hello\nworld\n"


def test_github_pr_git_failure_no_pr(tmp_path):
    remote = make_bare_remote(tmp_path)
    patch = tmp_path / "bad.patch"
    patch.write_text("this is not a patch\n", encoding="utf-8")
    github = FakeGithub(remote)
    sender = GithubPrSender(github, username="bot")
    item = OutboxItem(
        id="z",
        mission="demo",
        strategy_id=None,
        kind="github_pr",
        target="o/r",
        body="fix",
        links_json="[]",
        payload_json=json.dumps(
            {
                "repo": "o/r",
                "base": "main",
                "branch": "scav-fix-bad",
                "title": "Bad",
                "patch_path": str(patch),
            }
        ),
        cost=Decimal(0),
        content_sha256="s",
        expires_at="2030-01-01T00:00:00+00:00",
        status="approved",
        decided_at=None,
        decided_via=None,
        decided_by=None,
        sent_at=None,
        send_result_json=None,
        created_at="2026-10-04T11:00:00+00:00",
    )
    result = sender.send(item)
    assert result.ok is False
    assert github.prs == []


def test_manual_requires_mark_sent(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    item = outbox.create_draft(
        DraftSpec(kind="manual", target="some-form", body="fill this"),
        "demo",
        None,
    )
    outbox.approve(item.id, None, via="cli", by="human")
    senders = {"manual": ManualSender()}
    send_approved(store, senders, outbox)
    assert store.get_outbox(item.id).status == "approved"


def test_unapproved_items_never_sent(tmp_path, monkeypatch):
    config, store = setup(tmp_path, monkeypatch)
    outbox = create_outbox(store, config, [], FakeClock())
    outbox.create_draft(
        DraftSpec(kind="email", target="boss@example.com", body="hi"),
        "demo",
        None,
    )
    smtp = FakeSmtp()
    send_approved(store, {"email": EmailSender(smtp, "me@example.com")}, outbox)
    assert smtp.sent == []


def test_github_comment_sender(tmp_path):
    github = FakeGithub(tmp_path)
    sender = GithubCommentSender(github, username="bot")
    item = OutboxItem(
        id="c",
        mission="demo",
        strategy_id=None,
        kind="github_comment",
        target="https://github.com/o/r/issues/3",
        body="ping",
        links_json="[]",
        payload_json="{}",
        cost=Decimal(0),
        content_sha256="s",
        expires_at="2030-01-01T00:00:00+00:00",
        status="approved",
        decided_at=None,
        decided_via=None,
        decided_by=None,
        sent_at=None,
        send_result_json=None,
        created_at="2026-10-04T11:00:00+00:00",
    )
    result = sender.send(item)
    assert result.ok is True
    assert result.locator["issue"] == 3
    assert result.locator["author"] == "bot"
