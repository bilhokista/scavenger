import json
from decimal import Decimal

from scavenger import clock
from scavenger.channels import Action, Liveness
from scavenger.outbox import DraftSpec
from scavenger.switcher import Candidate


class BountyBoardChannel:
    # Generic issue-bounty discovery. No platform qualified for automated
    # discovery on survey day (see docs/channels/LIVENESS.md), so items
    # come from configuration (human-pasted issue links), each resolved
    # through the GitHub client. Providers per platform plug in here when
    # one qualifies.
    name = "bounty"

    def __init__(
        self, github_client, items, username: str, max_open_prs: int = 3
    ) -> None:
        self._github = github_client
        self._items = list(items)
        self._username = username
        self._max_open_prs = max_open_prs

    def liveness(self) -> Liveness:
        return Liveness(
            alive=True,
            evidence_urls=(),
            detail="configured issue list",
            checked_at=clock.now(),
        )

    def discover(self, goal) -> list:
        found = []
        for item in self._items:
            repo, number = item["repo"], item["issue"]
            details = self._github.get_issue(repo, number)
            if details is None or details.get("state") != "open":
                continue
            rivals = [
                pr
                for pr in self._github.prs_for_issue(repo, number)
                if pr.get("author") != self._username
                and pr.get("state") == "open"
                and pr.get("ci_passing")
            ]
            if len(rivals) >= 2:
                continue
            if self._github.count_open_prs(repo, self._username) >= (
                self._max_open_prs
            ):
                continue
            found.append(
                Candidate(
                    channel="bounty",
                    path=f"{repo}#{number}",
                    path_key=f"bounty:{repo}#{number}",
                    outward_key=f"github:{repo}",
                    payout_amount=Decimal(str(item["amount"])),
                    payout_currency=item["currency"],
                    guarantor=item.get("guarantor", "escrow"),
                    guarantor_evidence=item.get("evidence"),
                    capital_needed=item.get("capital_needed", Decimal(0)),
                    hours_first_proof=item.get("hours_first_proof", 24.0),
                    hours_settlement=item.get("hours_settlement", 168.0),
                    probability=item.get("probability", 0.3),
                    probability_note="configured issue, no ledger history",
                    source_urls=(item.get("url", ""),),
                    rungs=(
                        {
                            "rung": "pr_open",
                            "adapter": "github",
                            "locator": {"repo": repo},
                        },
                        {
                            "rung": "pr_merged",
                            "adapter": "github",
                            "locator": {"repo": repo},
                        },
                        {"rung": "settled", "adapter": "payment_email", "locator": {}},
                    ),
                )
            )
        return found

    def next_action(self, strategy, last_check) -> Action:
        rung = json.loads(strategy.rungs_json)[strategy.rung_index]["rung"]
        if rung == "pr_open":
            return Action(
                kind="local",
                draft=None,
                description=(f"Fix {strategy.path_key}. Write the patch, never push."),
                fingerprint=f"bounty-local:{strategy.path_key}",
            )
        if rung == "pr_merged":
            if last_check is not None and "?" in (last_check.evidence_raw or ""):
                question = last_check.evidence_raw.strip().splitlines()[-1]
                return Action(
                    kind="draft",
                    draft=DraftSpec(
                        kind="github_comment",
                        target=strategy.outward_key,
                        body=(
                            "Maintainer asked:\n\n"
                            f"{question}\n\n---\n"
                            "Write the reply above this line, then redraft."
                        ),
                    ),
                    description="answer maintainer question",
                    fingerprint=f"bounty-comment:{strategy.path_key}",
                )
            return Action(
                kind="wait",
                draft=None,
                description="wait for review",
                fingerprint=f"bounty-wait:{strategy.path_key}",
            )
        return Action(
            kind="wait",
            draft=None,
            description="wait for payout",
            fingerprint=f"bounty-settle:{strategy.path_key}",
        )
