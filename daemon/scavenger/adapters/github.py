from datetime import datetime

import httpx

from scavenger.adapters import Adapter, ProofResult, ProofState

_PR_RUNG_PASS = frozenset({"pr_open", "submitted"})
_MERGED_RUNG = frozenset({"pr_merged", "accepted"})


class GitHubAdapter(Adapter):
    name = "github"

    def __init__(
        self,
        token: str,
        timeout: float = 20.0,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._token = token
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    def _get(self, path: str) -> httpx.Response | None:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(
                    f"{self._base_url}{path}",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": "application/vnd.github+json",
                    },
                )
        except httpx.HTTPError:
            return None
        return response

    def _unverified(self, detail: str) -> ProofResult:
        return ProofResult(state=ProofState.UNVERIFIED, evidence_raw="", detail=detail)

    def check(self, rung: str, locator: dict, since: datetime) -> ProofResult:
        repo = locator["repo"]
        if "pr" in locator:
            number = locator["pr"]
            if rung == "replied":
                return self._check_replied(
                    repo,
                    number,
                    locator.get("author", ""),
                    since,
                    include_reviews=True,
                )
            response = self._get(f"/repos/{repo}/pulls/{number}")
            if response is None:
                return self._unverified("network error")
            return self._check_pull(rung, response)
        if "issue" in locator:
            return self._check_replied(
                repo,
                locator["issue"],
                locator.get("author", ""),
                since,
                include_reviews=False,
            )
        return self._unverified(f"locator needs pr or issue: {locator!r}")

    def _check_pull(self, rung: str, response: httpx.Response) -> ProofResult:
        raw = response.text
        if response.status_code == 404:
            return ProofResult(
                state=ProofState.FAIL,
                evidence_raw=raw,
                detail="pull request not found",
            )
        if response.status_code == 403 and (
            response.headers.get("X-RateLimit-Remaining") == "0"
        ):
            return self._unverified("rate limited")
        if response.status_code >= 400:
            return self._unverified(f"http {response.status_code}")
        body = response.json()
        if rung in _PR_RUNG_PASS:
            return ProofResult(
                state=ProofState.PASS,
                evidence_raw=raw,
                detail="pull request exists",
            )
        if rung in _MERGED_RUNG:
            if body.get("merged"):
                return ProofResult(
                    state=ProofState.PASS,
                    evidence_raw=raw,
                    detail="pull request merged",
                )
            if body.get("state") == "open":
                return ProofResult(
                    state=ProofState.PENDING,
                    evidence_raw=raw,
                    detail="pull request still open",
                )
            return ProofResult(
                state=ProofState.FAIL,
                evidence_raw=raw,
                detail="pull request closed unmerged",
            )
        return self._unverified(f"unknown rung: {rung}")

    def _check_replied(
        self,
        repo: str,
        number: int,
        author: str,
        since: datetime,
        include_reviews: bool,
    ) -> ProofResult:
        response = self._get(f"/repos/{repo}/issues/{number}/comments")
        if response is None or response.status_code >= 400:
            return self._unverified("cannot fetch comments")
        bodies = [response.text]
        try:
            comments = response.json()
        except ValueError:
            return self._unverified("bad comment payload")
        found = self._fresh_comment(comments, author, since)
        if include_reviews and found is None:
            reviews = self._get(f"/repos/{repo}/pulls/{number}/reviews")
            if reviews is None or reviews.status_code >= 400:
                return self._unverified("cannot fetch reviews")
            bodies.append(reviews.text)
            try:
                found = self._fresh_comment(reviews.json(), author, since)
            except ValueError:
                return self._unverified("bad review payload")
        raw = "\n".join(bodies)
        if found is not None:
            return ProofResult(
                state=ProofState.PASS,
                evidence_raw=raw,
                detail=f"reply by {found}",
            )
        return ProofResult(
            state=ProofState.PENDING,
            evidence_raw=raw,
            detail="no reply yet",
        )

    @staticmethod
    def _fresh_comment(items: list, author: str, since: datetime) -> str | None:
        for item in items:
            login = (item.get("user") or {}).get("login", "")
            if login == author:
                continue
            try:
                created = datetime.fromisoformat(item.get("created_at", ""))
            except ValueError:
                continue
            if created > since:
                return login
        return None
