from datetime import UTC, datetime

import httpx
import respx
from scavenger.adapters import ProofState
from scavenger.adapters.github import GitHubAdapter

SINCE = datetime(2026, 10, 3, 5, 0, tzinfo=UTC)


def make_adapter():
    return GitHubAdapter(token="test-token")


@respx.mock
def test_pr_open_exists_passes(respx_mock):
    respx_mock.get("https://api.github.com/repos/o/r/pulls/12").mock(
        return_value=httpx.Response(200, json={"state": "open", "merged": False})
    )
    result = make_adapter().check("submitted", {"repo": "o/r", "pr": 12}, SINCE)
    assert result.state == ProofState.PASS


@respx.mock
def test_pr_open_missing_fails(respx_mock):
    respx_mock.get("https://api.github.com/repos/o/r/pulls/12").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    result = make_adapter().check("pr_open", {"repo": "o/r", "pr": 12}, SINCE)
    assert result.state == ProofState.FAIL


@respx.mock
def test_pr_merged_passes(respx_mock):
    respx_mock.get("https://api.github.com/repos/o/r/pulls/12").mock(
        return_value=httpx.Response(200, json={"state": "closed", "merged": True})
    )
    result = make_adapter().check("pr_merged", {"repo": "o/r", "pr": 12}, SINCE)
    assert result.state == ProofState.PASS


@respx.mock
def test_pr_merged_open_pending(respx_mock):
    respx_mock.get("https://api.github.com/repos/o/r/pulls/12").mock(
        return_value=httpx.Response(200, json={"state": "open", "merged": False})
    )
    result = make_adapter().check("accepted", {"repo": "o/r", "pr": 12}, SINCE)
    assert result.state == ProofState.PENDING


@respx.mock
def test_pr_merged_closed_unmerged_fails(respx_mock):
    respx_mock.get("https://api.github.com/repos/o/r/pulls/12").mock(
        return_value=httpx.Response(200, json={"state": "closed", "merged": False})
    )
    result = make_adapter().check("pr_merged", {"repo": "o/r", "pr": 12}, SINCE)
    assert result.state == ProofState.FAIL


@respx.mock
def test_replied_on_pr_passes(respx_mock):
    respx_mock.get("https://api.github.com/repos/o/r/issues/12/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "user": {"login": "maintainer"},
                    "created_at": "2026-10-03T06:00:00Z",
                    "body": "looks good",
                }
            ],
        )
    )
    respx_mock.get("https://api.github.com/repos/o/r/pulls/12/reviews").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = make_adapter().check(
        "replied", {"repo": "o/r", "pr": 12, "author": "me"}, SINCE
    )
    assert result.state == ProofState.PASS


@respx.mock
def test_replied_on_issue_passes(respx_mock):
    respx_mock.get("https://api.github.com/repos/o/r/issues/7/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "user": {"login": "other"},
                    "created_at": "2026-10-03T06:00:00Z",
                    "body": "noted",
                }
            ],
        )
    )
    result = make_adapter().check(
        "replied", {"repo": "o/r", "issue": 7, "author": "me"}, SINCE
    )
    assert result.state == ProofState.PASS


@respx.mock
def test_replied_none_pending(respx_mock):
    respx_mock.get("https://api.github.com/repos/o/r/issues/12/comments").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "user": {"login": "me"},
                    "created_at": "2026-10-03T06:00:00Z",
                    "body": "ping",
                }
            ],
        )
    )
    respx_mock.get("https://api.github.com/repos/o/r/pulls/12/reviews").mock(
        return_value=httpx.Response(200, json=[])
    )
    result = make_adapter().check(
        "replied", {"repo": "o/r", "pr": 12, "author": "me"}, SINCE
    )
    assert result.state == ProofState.PENDING


@respx.mock
def test_rate_limit_is_unverified(respx_mock):
    respx_mock.get("https://api.github.com/repos/o/r/pulls/12").mock(
        return_value=httpx.Response(
            403,
            headers={"X-RateLimit-Remaining": "0"},
            json={"message": "rate limited"},
        )
    )
    result = make_adapter().check("submitted", {"repo": "o/r", "pr": 12}, SINCE)
    assert result.state == ProofState.UNVERIFIED


@respx.mock
def test_timeout_is_unverified(respx_mock):
    route = respx_mock.get("https://api.github.com/repos/o/r/pulls/12")
    route.mock(side_effect=httpx.ConnectTimeout("slow"))
    result = make_adapter().check("submitted", {"repo": "o/r", "pr": 12}, SINCE)
    assert result.state == ProofState.UNVERIFIED


@respx.mock
def test_evidence_is_raw_response(respx_mock):
    body = {"state": "open", "merged": False, "number": 12}
    respx_mock.get("https://api.github.com/repos/o/r/pulls/12").mock(
        return_value=httpx.Response(200, json=body)
    )
    result = make_adapter().check("submitted", {"repo": "o/r", "pr": 12}, SINCE)
    assert '"number": 12' in result.evidence_raw or '"number":12' in (
        result.evidence_raw.replace(" ", "")
    )
