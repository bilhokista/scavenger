import httpx

from scavenger.notifiers import Notice, NotifyError


class SlackNotifier:
    name = "slack"
    supports_replies = False

    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    def notify(self, notice: Notice) -> None:
        try:
            response = httpx.post(
                self._webhook_url,
                json={"text": f"*{notice.title}*\n{notice.body}"},
                timeout=20,
            )
        except httpx.HTTPError as error:
            raise NotifyError(f"slack failed: {error}") from error
        if response.status_code >= 400:
            raise NotifyError(f"slack http {response.status_code}")
