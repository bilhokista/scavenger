import json
from decimal import Decimal

from scavenger.channels import Liveness
from scavenger.channels.bounty_board import BountyBoardChannel


class FakeGithub:
    def __init__(self, issues=None, prs=None, open_prs=0, username="bot"):
        self._issues = issues or {}
        self._prs = prs or {}
        self._open_prs = open_prs
        self.username = username

    def get_issue(self, repo, number):
        return self._issues.get((repo, number))

    def prs_for_issue(self, repo, number):
        return self._prs.get((repo, number), [])

    def count_open_prs(self, repo, author):
        return self._open_prs


def make_channel(github, items=None):
    return BountyBoardChannel(
        github_client=github,
        items=items
        if items is not None
        else [
            {
                "repo": "o/r",
                "issue": 12,
                "amount": "100.00",
                "currency": "USD",
                "guarantor": "escrow",
                "evidence": "https://grants.example/1",
            }
        ],
        username="bot",
        max_open_prs=3,
    )


def test_discover_builds_candidate_with_three_rungs():
    github = FakeGithub(issues={("o/r", 12): {"state": "open"}})
    channel = make_channel(github)
    candidates = channel.discover(None)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.payout_amount == Decimal("100.00")
    assert candidate.path_key == "bounty:o/r#12"
    assert [rung["rung"] for rung in candidate.rungs] == [
        "pr_open",
        "pr_merged",
        "settled",
    ]


def test_skip_issue_with_two_competing_prs():
    prs = [
        {"author": "alice", "state": "open", "ci_passing": True},
        {"author": "bob", "state": "open", "ci_passing": True},
    ]
    github = FakeGithub(issues={("o/r", 12): {"state": "open"}}, prs={("o/r", 12): prs})
    assert make_channel(github).discover(None) == []


def test_skip_repo_at_pr_cap():
    github = FakeGithub(issues={("o/r", 12): {"state": "open"}}, open_prs=3)
    assert make_channel(github).discover(None) == []


def test_skip_closed_issue():
    github = FakeGithub(issues={("o/r", 12): {"state": "closed"}})
    assert make_channel(github).discover(None) == []


def test_next_action_rung_zero_is_local():
    channel = make_channel(FakeGithub())
    from scavenger.store import Strategy

    strategy = Strategy(
        id=1,
        mission="demo",
        channel="bounty",
        path="p",
        path_key="bounty:o/r#12",
        outward_key="github:o/r",
        payout_amount=Decimal(100),
        payout_currency="USD",
        guarantor="escrow",
        guarantor_evidence="https://grants.example/1",
        capital_needed=Decimal(0),
        hours_first_proof=1.0,
        hours_settlement=24.0,
        probability=0.5,
        probability_source="estimate",
        probability_note="n",
        score=Decimal(1),
        rungs_json=RUNGS_JSON,
        status="active",
        rung_index=0,
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
    action = channel.next_action(strategy, None)
    assert action.kind == "local"


def test_next_action_rung_one_waits():
    channel = make_channel(FakeGithub())
    action = channel.next_action(_strategy_at_rung(1), None)
    assert action.kind == "wait"


def test_next_action_comment_on_maintainer_question():
    from scavenger.store import ProofCheck

    channel = make_channel(FakeGithub())
    check = ProofCheck(
        id=9,
        strategy_id=1,
        round_id=1,
        rung="pr_merged",
        adapter="github",
        locator_json="{}",
        state="pass",
        evidence_raw="maintainer: can you rebase?",
        evidence_sha256="s",
        error=None,
        checked_at="2026-10-04T12:00:00+00:00",
    )
    action = channel.next_action(_strategy_at_rung(1), check)
    assert action.kind == "draft"
    assert action.draft.kind == "github_comment"


def test_next_action_rung_two_waits():
    channel = make_channel(FakeGithub())
    action = channel.next_action(_strategy_at_rung(2), None)
    assert action.kind == "wait"


def test_liveness_alive():
    channel = make_channel(FakeGithub())
    state = channel.liveness()
    assert isinstance(state, Liveness)
    assert state.alive is True


RUNGS_JSON = json.dumps(
    [
        {"rung": "pr_open", "adapter": "github", "locator": {}},
        {"rung": "pr_merged", "adapter": "github", "locator": {}},
        {"rung": "settled", "adapter": "payment_email", "locator": {}},
    ]
)


def _strategy_at_rung(index):
    from scavenger.store import Strategy

    return Strategy(
        id=1,
        mission="demo",
        channel="bounty",
        path="p",
        path_key="bounty:o/r#12",
        outward_key="github:o/r",
        payout_amount=Decimal(100),
        payout_currency="USD",
        guarantor="escrow",
        guarantor_evidence="https://grants.example/1",
        capital_needed=Decimal(0),
        hours_first_proof=1.0,
        hours_settlement=24.0,
        probability=0.5,
        probability_source="estimate",
        probability_note="n",
        score=Decimal(1),
        rungs_json=RUNGS_JSON,
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
