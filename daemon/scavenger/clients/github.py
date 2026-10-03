import httpx


class GithubError(Exception):
    pass


class GithubRestClient:
    def __init__(
        self,
        token: str,
        timeout: float = 20.0,
        username: str = "",
        base_url: str = "https://api.github.com",
    ) -> None:
        self.username = username
        self._token = token
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    def _headers(self) -> dict:
        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _get(self, path: str, params=None):
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(
                    f"{self._base_url}{path}",
                    headers=self._headers(),
                    params=params or {},
                )
        except httpx.HTTPError as error:
            raise GithubError(f"github GET failed: {error}") from error
        if response.status_code >= 400:
            raise GithubError(f"github GET {path}: {response.status_code}")
        return response.json()

    def _post(self, path: str, payload: dict):
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    f"{self._base_url}{path}",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as error:
            raise GithubError(f"github POST failed: {error}") from error
        if response.status_code >= 400:
            raise GithubError(f"github POST {path}: {response.status_code}")
        return response.json()

    def get_issue(self, repo: str, number: int):
        try:
            body = self._get(f"/repos/{repo}/issues/{number}")
        except GithubError:
            return None
        if body.get("pull_request") is not None:
            return None
        return {"state": body.get("state"), "title": body.get("title", "")}

    def prs_for_issue(self, repo: str, number: int) -> list:
        try:
            pulls = self._get(
                f"/repos/{repo}/pulls",
                {"state": "open", "per_page": "30"},
            )
        except GithubError:
            return []
        found = []
        for pull in pulls:
            body = pull.get("body") or ""
            if f"#{number}" not in body:
                continue
            found.append(
                {
                    "author": (pull.get("user") or {}).get("login", ""),
                    "state": pull.get("state", ""),
                    "ci_passing": self._ci_passing(
                        repo, pull.get("head", {}).get("sha", "")
                    ),
                }
            )
        return found

    def _ci_passing(self, repo: str, sha: str) -> bool:
        if not sha:
            return False
        try:
            runs = self._get(
                f"/repos/{repo}/commits/{sha}/check-runs",
                {"per_page": "30"},
            )
        except GithubError:
            return False
        conclusions = [run.get("conclusion") for run in runs.get("check_runs", [])]
        if not conclusions:
            return False
        return all(
            conclusion in ("success", "neutral", "skipped")
            for conclusion in conclusions
        )

    def count_open_prs(self, repo: str, author: str) -> int:
        body = self._get(
            "/search/issues",
            {
                "q": f"repo:{repo} author:{author} state:open type:pr",
                "per_page": "1",
            },
        )
        return body.get("total_count", 0)

    def ensure_fork(self, repo: str) -> str:
        try:
            body = self._post(f"/repos/{repo}/forks", {})
        except GithubError as error:
            if "202" in str(error) or "already" in str(error).lower():
                pass
            else:
                raise
            body = {}
        full_name = body.get("full_name")
        if full_name:
            return full_name
        return f"{self.username}/{repo.split('/')[-1]}"

    def clone_url(self, full_name: str) -> str:
        if self._token:
            return f"https://x-access-token:{self._token}@github.com/{full_name}.git"
        return f"https://github.com/{full_name}.git"

    def create_pr(self, repo: str, title: str, head: str, base: str) -> int:
        body = self._post(
            f"/repos/{repo}/pulls",
            {"title": title, "head": head, "base": base},
        )
        return body["number"]

    def post_comment(self, issue_url: str, body: str) -> dict:
        parts = issue_url.rstrip("/").split("/")
        number = int(parts[-1])
        repo = "/".join(parts[-4:-2]) if "issues" in parts else ""
        if not repo:
            raise GithubError(f"cannot parse issue url: {issue_url}")
        posted = self._post(f"/repos/{repo}/issues/{number}/comments", {"body": body})
        return {"repo": repo, "issue": posted.get("number", number)}
