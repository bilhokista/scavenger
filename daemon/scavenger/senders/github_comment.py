import json

from scavenger.senders import SendResult


class GithubCommentSender:
    kind = "github_comment"

    def __init__(self, github, username: str) -> None:
        self.github = github
        self._username = username

    def send(self, item) -> SendResult:
        try:
            payload = json.loads(item.payload_json)
        except ValueError:
            payload = {}
        issue_url = payload.get("issue_url", item.target)
        posted = self.github.post_comment(issue_url, item.body)
        return SendResult(
            ok=True,
            locator={
                "repo": posted["repo"],
                "issue": posted["issue"],
                "author": self._username,
            },
            detail=f"comment on {issue_url}",
        )
