import httpx

from scavenger.notifiers import Notice, NotifyError


class NtfyNotifier:
    name = "ntfy"
    supports_replies = False

    def __init__(self, server: str, topic: str) -> None:
        self._server = server.rstrip("/")
        self._topic = topic

    def notify(self, notice: Notice) -> None:
        try:
            response = httpx.post(
                f"{self._server}/{self._topic}",
                content=f"{notice.title}\n{notice.body}".encode(),
                headers={"Title": notice.title},
                timeout=20,
            )
        except httpx.HTTPError as error:
            raise NotifyError(f"ntfy failed: {error}") from error
        if response.status_code >= 400:
            raise NotifyError(f"ntfy http {response.status_code}")
