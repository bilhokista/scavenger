from datetime import UTC, datetime
from decimal import Decimal

from scavenger.channels.client_inbox import ClientInboxChannel
from scavenger.config import InboxFilter
from scavenger.llm import LLMResult
from scavenger.store import Strategy

from tests.conftest import FakeLLM

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Jobs</title>
<item>
<title>Need a Python bot ($800 fixed)</title>
<link>https://jobs.example/abc</link>
<pubDate>Sat, 03 Oct 2026 05:00:00 GMT</pubDate>
<description>Build a scraper. Apply via the form.</description>
</item>
<item>
<title>Old post with no link</title>
<link></link>
<pubDate>Sat, 03 Oct 2026 05:00:00 GMT</pubDate>
<description>Stale.</description>
</item>
</channel></rss>
"""


class FakeImap:
    def __init__(self, messages):
        self._messages = messages

    def list_since(self, since):
        return [message for message in self._messages if message["date"] >= since]


class FakeHttp:
    def __init__(self, bodies):
        self._bodies = bodies

    def get(self, url):
        class Response:
            status_code = 200

            def __init__(self, text):
                self.text = text

        return Response(self._bodies[url])


class FakeGoal:
    statement = "rent of 1500 USD due"
    target_amount = Decimal("1500.00")


def inbound(**overrides):
    message = {
        "message_id": "<req-1@client.example>",
        "from_addr": "client@example.com",
        "date": datetime(2026, 10, 3, 6, 0, tzinfo=UTC),
        "subject": "Freelance project inquiry",
        "text": "Need a dashboard, budget $2,000. Are you available?",
    }
    message.update(overrides)
    return message


def make_channel(messages=(), feeds=True):
    http = FakeHttp({"https://jobs.example/feed.rss": FEED_XML} if feeds else {})
    llm = FakeLLM([LLMResult(text="Proposal text.", tokens_in=5, tokens_out=10)] * 10)
    return ClientInboxChannel(
        imap_client=FakeImap(list(messages)),
        feeds=["https://jobs.example/feed.rss"] if feeds else [],
        http_client=http,
        filters=[InboxFilter(subject_regex="(?i)freelance|inquiry", body_regex="")],
        llm=llm,
    )


def test_inbound_email_matching_filter_becomes_candidate():
    channel = make_channel([inbound()], feeds=False)
    candidates = channel.discover(FakeGoal())
    assert len(candidates) == 1
    candidate = candidates[0]
    assert "req-1" in candidate.path_key
    assert candidate.outward_key == "email:client@example.com"
    assert candidate.payout_amount == Decimal(2000)
    assert candidate.guarantor == "none"


def test_non_matching_email_ignored():
    channel = make_channel(
        [inbound(subject="Hello friend", text="Just saying hi")], feeds=False
    )
    assert channel.discover(FakeGoal()) == []


def test_no_candidate_without_cited_request():
    channel = make_channel([], feeds=True)
    candidates = channel.discover(FakeGoal())
    assert len(candidates) == 1
    assert candidates[0].path_key == "jobs:https://jobs.example/abc"


def test_next_action_email_draft():
    channel = make_channel([inbound()], feeds=False)
    channel.discover(FakeGoal())
    action = channel.next_action(_strategy_at_rung(0, "email:client@example.com"), None)
    assert action.kind == "draft"
    assert action.draft.kind == "email"
    assert action.draft.target == "client@example.com"
    assert "Proposal text." in action.draft.body


def test_next_action_manual_draft_for_form():
    channel = make_channel([], feeds=True)
    channel.discover(FakeGoal())
    action = channel.next_action(
        _strategy_at_rung(0, "form:https://jobs.example/abc"), None
    )
    assert action.kind == "draft"
    assert action.draft.kind == "manual"


def test_next_action_later_rungs_wait():
    channel = make_channel([inbound()], feeds=False)
    channel.discover(FakeGoal())
    for index in (1, 2, 3):
        action = channel.next_action(
            _strategy_at_rung(index, "email:client@example.com"), None
        )
        assert action.kind == "wait"


def _strategy_at_rung(index, outward_key):
    import json

    rungs = json.dumps(
        [
            {"rung": "submitted", "adapter": "attested", "locator": {}},
            {"rung": "replied", "adapter": "inbox", "locator": {}},
            {"rung": "deposit", "adapter": "payment_email", "locator": {}},
            {"rung": "settled", "adapter": "payment_email", "locator": {}},
        ]
    )
    return Strategy(
        id=1,
        mission="demo",
        channel="client-inbox",
        path="p",
        path_key="inbox:req",
        outward_key=outward_key,
        payout_amount=Decimal(2000),
        payout_currency="USD",
        guarantor="none",
        guarantor_evidence=None,
        capital_needed=Decimal(0),
        hours_first_proof=1.0,
        hours_settlement=24.0,
        probability=0.5,
        probability_source="estimate",
        probability_note="n",
        score=Decimal(1),
        rungs_json=rungs,
        status="active",
        rung_index=index,
        failed_rounds=0,
        loss=Decimal(0),
        pending_since=None,
        unverified_streak=0,
        next_check_at=None,
        created_at="2026-10-04T12:00:00+00:00",
        closed_at=None,
        death_cause=None,
        death_evidence=None,
    )
