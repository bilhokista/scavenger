import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from scavenger.notifiers import Notice, NotifyError

_COMMAND_RE = re.compile(r"^/(approve|reject) ([0-9a-f]{32}) ([A-Z2-7]{20})$")


@dataclass(frozen=True)
class TelegramReply:
    command: str
    outbox_id: str
    token: str
    user_id: int


class TelegramNotifier:
    name = "telegram"
    supports_replies = True

    def __init__(self, bot_token: str, chat_id: str, allowed_user_ids, run_dir) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._allowed = set(allowed_user_ids)
        self._run_dir = Path(run_dir)

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def notify(self, notice: Notice) -> None:
        try:
            response = httpx.post(
                self._api("sendMessage"),
                json={
                    "chat_id": self._chat_id,
                    "text": f"{notice.title}\n{notice.body}",
                },
                timeout=20,
            )
        except httpx.HTTPError as error:
            raise NotifyError(f"telegram failed: {error}") from error
        if response.status_code >= 400:
            raise NotifyError(f"telegram http {response.status_code}")

    def poll_replies(self) -> list:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        offset_file = self._run_dir / "telegram_offset"
        try:
            offset = int(offset_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            offset = 0
        try:
            response = httpx.get(
                self._api("getUpdates"),
                params={"offset": offset, "timeout": 0},
                timeout=20,
            )
        except httpx.HTTPError as error:
            raise NotifyError(f"telegram poll failed: {error}") from error
        if response.status_code >= 400:
            raise NotifyError(f"telegram http {response.status_code}")
        replies = []
        newest = offset
        for update in response.json().get("result", []):
            newest = max(newest, update.get("update_id", 0) + 1)
            message = update.get("message", {})
            user_id = message.get("from", {}).get("id")
            if user_id not in self._allowed:
                continue
            match = _COMMAND_RE.match((message.get("text") or "").strip())
            if match is None:
                continue
            replies.append(
                TelegramReply(
                    command=match.group(1),
                    outbox_id=match.group(2),
                    token=match.group(3),
                    user_id=user_id,
                )
            )
        offset_file.write_text(str(newest), encoding="utf-8")
        return replies
