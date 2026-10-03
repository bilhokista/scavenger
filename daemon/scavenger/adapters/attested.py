from datetime import datetime

from scavenger.adapters import Adapter, ProofResult, ProofState


class AttestedAdapter(Adapter):
    # Passes when a human attested the submission through mark-sent,
    # which stores the proof URL in the round locator. Trusts the
    # human click, like the manual sender does.
    name = "attested"

    def check(self, rung: str, locator: dict, since: datetime) -> ProofResult:
        url = locator.get("application_url") or locator.get("url")
        if url:
            return ProofResult(
                state=ProofState.PASS,
                evidence_raw=url,
                detail="human-attested submission",
            )
        return ProofResult(
            state=ProofState.PENDING,
            evidence_raw="",
            detail="no submission attested yet",
        )
