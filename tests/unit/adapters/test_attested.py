from datetime import UTC, datetime

from scavenger.adapters import ProofState
from scavenger.adapters.attested import AttestedAdapter

SINCE = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def test_pass_with_proof_url():
    adapter = AttestedAdapter()
    result = adapter.check(
        "submitted",
        {"application_url": "https://program.example/a/1"},
        SINCE,
    )
    assert result.state == ProofState.PASS


def test_pending_without_proof_url():
    adapter = AttestedAdapter()
    result = adapter.check("submitted", {}, SINCE)
    assert result.state == ProofState.PENDING
