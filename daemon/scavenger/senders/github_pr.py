import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from scavenger.senders import SendResult


class GithubPrSender:
    kind = "github_pr"

    def __init__(self, github, username: str) -> None:
        self.github = github
        self._username = username

    def send(self, item) -> SendResult:
        try:
            payload = json.loads(item.payload_json)
            repo = payload["repo"]
            base = payload.get("base", "main")
            branch = payload["branch"]
            title = payload["title"]
            patch_path = payload["patch_path"]
        except (ValueError, KeyError) as error:
            return SendResult(ok=False, locator={}, detail=f"bad payload: {error}")
        workdir = Path(tempfile.mkdtemp(prefix="scavenger-pr-"))
        try:
            fork = self.github.ensure_fork(repo)
            self._run(["git", "clone", self.github.clone_url(fork), "."], workdir)
            self._run(["git", "checkout", "-B", branch], workdir)
            self._run(["git", "apply", "--whitespace=fix", patch_path], workdir)
            self._run(["git", "add", "-A"], workdir)
            self._run(
                [
                    "git",
                    "-c",
                    "user.email=scavenger@localhost",
                    "-c",
                    "user.name=scavenger",
                    "commit",
                    "-m",
                    title,
                ],
                workdir,
            )
            self._run(["git", "push", "origin", branch], workdir)
            number = self.github.create_pr(
                repo, title, f"{self._username}:{branch}", base
            )
        except (subprocess.CalledProcessError, OSError) as error:
            return SendResult(ok=False, locator={}, detail=str(error))
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return SendResult(
            ok=True,
            locator={"repo": repo, "pr": number},
            detail=f"PR #{number} in {repo}",
        )

    @staticmethod
    def _run(args: list, cwd: Path) -> None:
        subprocess.run(args, cwd=cwd, capture_output=True, check=True)
