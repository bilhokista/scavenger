import smtplib
from email.mime.text import MIMEText

from scavenger.notifiers import Notice, NotifyError


class SmtpClient:
    def __init__(
        self, host: str, port: int, username: str, password: str, from_address: str
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_address = from_address

    def send(
        self, to: str, subject: str, body: str, message_id: str | None = None
    ) -> None:
        message = MIMEText(body, "plain", "utf-8")
        message["From"] = self._from_address
        message["To"] = to
        message["Subject"] = subject
        if message_id is not None:
            message["Message-ID"] = message_id
        try:
            with smtplib.SMTP(self._host, self._port, timeout=25) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(self._username, self._password)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as error:
            raise NotifyError(f"smtp failed: {error}") from error


class EmailNotifier:
    name = "email"
    supports_replies = False

    def __init__(self, client, to: str) -> None:
        self._client = client
        self._to = to

    def notify(self, notice: Notice) -> None:
        try:
            self._client.send(self._to, notice.title, notice.body)
        except NotifyError:
            raise
        except Exception as error:
            raise NotifyError(f"email failed: {error}") from error
