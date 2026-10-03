import shutil
import subprocess
import sys

from scavenger.notifiers import Notice, NotifyError


class DesktopNotifier:
    name = "desktop"
    supports_replies = False

    def __init__(self, store=None, mission=None) -> None:
        self._store = store
        self._mission = mission

    def notify(self, notice: Notice) -> None:
        try:
            if sys.platform == "linux":
                binary = shutil.which("notify-send")
                if binary is None:
                    raise NotifyError("notify-send missing")
                subprocess.run(
                    [binary, notice.title, notice.body],
                    capture_output=True,
                    check=True,
                )
            elif sys.platform == "darwin":
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        (
                            f'display notification "{notice.body}"'
                            f' with title "{notice.title}"'
                        ),
                    ],
                    capture_output=True,
                    check=True,
                )
            elif sys.platform == "win32":
                script = (
                    "[Windows.UI.Notifications.ToastNotificationManager,"
                    " Windows.UI.Notifications, ContentType ="
                    " WindowsRuntime] | Out-Null; "
                    "$t = [Windows.UI.Notifications.ToastTemplateType]::"
                    "ToastText02; "
                    f"$x = [Windows.UI.Notifications.ToastNotificationManager]::"
                    f"GetTemplateContent($t); "
                    f"$x.GetElementsByTagName('text')[0].AppendChild("
                    f"$x.CreateTextNode('{notice.title}'));"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", script],
                    capture_output=True,
                    check=True,
                )
            else:
                raise NotifyError(f"unsupported platform: {sys.platform}")
        except NotifyError as error:
            if self._store is not None:
                self._store.add_event(
                    mission=self._mission,
                    level="warn",
                    kind="notify_skipped",
                    message=str(error),
                )
