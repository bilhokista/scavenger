import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import timedelta

from scavenger import clock, report, store
from scavenger.notifiers import Notice


@dataclass(frozen=True)
class SupervisorResult:
    exit_code: int
    acted: bool
    triggers: tuple


def _read_pid(run_dir):
    try:
        return int((run_dir / "daemon.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
        )
    else:
        os.kill(pid, 15)


def _heartbeat_stale(run_dir, tick_seconds: int, factor: int) -> bool:
    try:
        stamp = (run_dir / "heartbeat").read_text(encoding="utf-8").strip()
    except OSError:
        return True
    from datetime import datetime

    try:
        seen = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    return clock.now() - seen > timedelta(seconds=factor * tick_seconds)


def run_once(cfg, notifiers=()) -> SupervisorResult:
    db = store.Store.open(cfg.paths.db, run_dir=cfg.paths.run_dir)
    triggers = []
    now = clock.now()
    stale = _heartbeat_stale(
        cfg.paths.run_dir,
        cfg.loop.tick_seconds,
        cfg.supervisor.heartbeat_stale_factor,
    )
    if stale:
        for notifier in notifiers:
            notifier.notify(
                Notice(
                    kind="escalation",
                    title="stale heartbeat",
                    body="no daemon heartbeat within the expected window",
                )
            )
    hour_ago = (now - timedelta(hours=1)).isoformat()
    spent = db.spend_since(None, hour_ago)
    if spent.money >= cfg.supervisor.max_hourly_money:
        triggers.append("hourly_money")
    if spent.tokens_in + spent.tokens_out >= cfg.supervisor.max_hourly_tokens:
        triggers.append("hourly_tokens")
    spun = []
    for mission in db.list_missions(("active",)):
        for strategy in db.list_strategies(mission.name):
            recent = db.list_rounds(strategy.id, cfg.supervisor.spin_rounds)
            if (
                len(recent) == cfg.supervisor.spin_rounds
                and len({round.action_fingerprint for round in recent}) == 1
                and recent[0].result_state not in (None, "pending")
                and len({round.result_state for round in recent}) == 1
            ):
                spun.append(mission.name)
                break
    if spun:
        triggers.append("spin")
    if stale:
        pid = _read_pid(cfg.paths.run_dir)
        if pid is not None and _process_exists(pid):
            triggers.append("stale_heartbeat")
    if not triggers:
        return SupervisorResult(exit_code=0, acted=False, triggers=())
    pid = _read_pid(cfg.paths.run_dir)
    if pid is not None:
        try:
            _kill(pid)
        except OSError:
            pass
    affected = {mission.name for mission in db.list_missions(("active",))}
    affected.update(spun)
    for name in sorted(affected):
        db.set_mission_status(
            name,
            "stopped_emergency",
            stopped_at=now.isoformat(),
            stop_reason=",".join(triggers),
        )
        report.write_report(db, name, cfg.paths.missions)
    for notifier in notifiers:
        notifier.notify(
            Notice(
                kind="emergency_stop",
                title="emergency stop",
                body=f"triggers: {','.join(triggers)}",
            )
        )
    return SupervisorResult(exit_code=3, acted=True, triggers=tuple(triggers))
