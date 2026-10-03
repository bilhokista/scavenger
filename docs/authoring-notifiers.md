# Authoring notifiers

A notifier delivers notices to a human. It never decides anything.

## Protocol (`daemon/scavenger/notifiers/__init__.py`)

- `name`, `supports_replies`, `notify(notice)`.
- All HTTP via `httpx`, all failures raise `NotifyError`.
- `fan_out` strips the approval token for notifiers without reply
  support and appends the local approve command instead.

## Minimal example

```python
import httpx
from scavenger.notifiers import Notice, NotifyError


class WebhookNotifier:
    name = "webhook"
    supports_replies = False

    def __init__(self, url: str) -> None:
        self._url = url

    def notify(self, notice: Notice) -> None:
        try:
            response = httpx.post(
                self._url,
                json={"text": f"{notice.title}\n{notice.body}"},
                timeout=20,
            )
        except httpx.HTTPError as error:
            raise NotifyError(f"webhook failed: {error}") from error
        if response.status_code >= 400:
            raise NotifyError(f"webhook http {response.status_code}")
```

## Required tests

- Delivery test with `respx` or a fake transport.
- Failure test asserting `NotifyError`.
- Reply-capable notifiers: allowlist test, malformed-command test,
  offset/state persistence test (see the Telegram tests).
