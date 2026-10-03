from decimal import Decimal

from scavenger.channels.grants import GrantsChannel
from scavenger.llm import LLMResult
from scavenger.store import Strategy

from tests.conftest import FakeLLM

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Grants</title>
<item>
<title>Builder round: $5,000 for Stellar tooling</title>
<link>https://rounds.example/stellar-tools</link>
<pubDate>Sat, 03 Oct 2026 05:00:00 GMT</pubDate>
<description>Apply with your project plan. Deadline in 60 days.</description>
</item>
<item>
<title>Meetup vol. 3 (no funding attached)</title>
<link>https://rounds.example/meetup-3</link>
<pubDate>Sat, 03 Oct 2026 05:00:00 GMT</pubDate>
<description>Come hang out.</description>
</item>
</channel></rss>
"""


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


def make_channel(feeds=True):
    http = FakeHttp({"https://feeds.example/rounds.rss": FEED_XML} if feeds else {})
    llm = FakeLLM([LLMResult(text="Dear committee...", tokens_in=5, tokens_out=10)])
    return GrantsChannel(
        feeds=["https://feeds.example/rounds.rss"] if feeds else [],
        http_client=http,
        llm=llm,
        rounds=[],
        default_window_days=90,
    ), llm


def test_discover_round_with_amount_and_future_deadline():
    channel, _ = make_channel()
    candidates = channel.discover(FakeGoal())
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.payout_amount == Decimal(5000)
    assert candidate.guarantor == "grant"
    assert [rung["rung"] for rung in candidate.rungs] == [
        "submitted",
        "accepted",
        "settled",
    ]


def test_discover_skips_item_without_amount():
    channel, _ = make_channel()
    candidates = channel.discover(FakeGoal())
    assert all("meetup" not in c.path for c in candidates)


def test_manual_round_entry():
    channel, _llm = make_channel(feeds=False)
    channel._rounds.append(
        {
            "name": "SCF round",
            "url": "https://rounds.example/scf",
            "amount": "20000",
            "currency": "USD",
            "deadline": "2026-12-31T00:00:00+00:00",
            "requirements": "Ship Stellar tooling.",
        }
    )
    candidates = channel.discover(FakeGoal())
    assert len(candidates) == 1
    assert candidates[0].payout_amount == Decimal(20000)


def test_next_action_rung_zero_manual_draft():
    channel, llm = make_channel()
    channel.discover(FakeGoal())
    strategy = _strategy_at_rung(0)
    action = channel.next_action(strategy, None)
    assert action.kind == "draft"
    assert action.draft.kind == "manual"
    assert "Dear committee" in action.draft.body
    assert llm.calls, "LLM writes the application text"


def test_next_action_later_rungs_wait():
    channel, _ = make_channel()
    channel.discover(FakeGoal())
    assert channel.next_action(_strategy_at_rung(1), None).kind == "wait"
    assert channel.next_action(_strategy_at_rung(2), None).kind == "wait"


def _strategy_at_rung(index):
    import json

    rungs = json.dumps(
        [
            {"rung": "submitted", "adapter": "attested", "locator": {}},
            {"rung": "accepted", "adapter": "inbox", "locator": {}},
            {"rung": "settled", "adapter": "payment_email", "locator": {}},
        ]
    )
    return Strategy(
        id=1,
        mission="demo",
        channel="grants",
        path="p",
        path_key="grants:x",
        outward_key="form:https://rounds.example/x",
        payout_amount=Decimal(5000),
        payout_currency="USD",
        guarantor="grant",
        guarantor_evidence="https://rounds.example/x",
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
