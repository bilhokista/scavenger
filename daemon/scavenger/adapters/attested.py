from datetime import datetime

from scavenger.adapters import Adapter, ProofResult, ProofState


class AttestedAdapter(Adapter):
    # Passes when a submission is attested: a human mark-sent URL or a
    # machine send receipt (thread id) in the round locator. Trusts the
    # attester, like the manual sender does.
    name = "attested"

    def check(self, rung: str, locator: dict, since: datetime) -> ProofResult:
        url = locator.get("application_url") or locator.get("url")
        if url:
            return ProofResult(
                state=ProofState.PASS,
                evidence_raw=url,
                detail="human-attested submission",
            )
        if locator.get("thread_message_id"):
            return ProofResult(
                state=ProofState.PASS,
                evidence_raw=locator["thread_message_id"],
                detail="sent receipt attested",
            )
        return ProofResult(
            state=ProofState.PENDING,
            evidence_raw="",
            detail="no submission attested yet",
        )
        return ProofResult(
            state=ProofState.PENDING,
            evidence_raw="",
            detail="no submission attested yet",
        )
