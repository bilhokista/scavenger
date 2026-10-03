import os
import time
from datetime import timedelta

from scavenger import brake, switcher
from scavenger.goal import parse as parse_goal
from scavenger.notifiers import Notice, fan_out
from scavenger.report import write_report
from scavenger.senders import send_approved
from scavenger.store import Store


class Loop:
    def __init__(
        self,
        store,
        config,
        verifier,
        executor,
        outbox,
        channels: dict,
        senders: dict,
        notifiers,
        research,
        clock,
    ) -> None:
        self._store = store
        self._config = config
        self._verifier = verifier
        self._executor = executor
        self._outbox = outbox
        self._channels = channels
        self._senders = senders
        self._notifiers = list(notifiers)
        self._research = research
        self._clock = clock
        self._liveness_checked = {}

    def tick(self, now=None) -> None:
        now = now or self._clock.now()
        for mission in self._store.list_missions(("active",)):
            try:
                self._tick_mission(mission, now)
            except Exception as error:  # noqa: BLE001 - one mission never kills the tick
                self._store.add_event(
                    mission=mission.name,
                    level="error",
                    kind="tick_failed",
                    message=str(error)[:500],
                )

    def _tick_mission(self, mission, now) -> None:
        self._check_mission_stop(mission, now)
        if self._store.get_mission(mission.name).status != "active":
            return
        self._collect_replies(mission)
        self._fail_rejected_rounds(mission)
        self._expire_drafts(mission)
        send_approved(self._store, self._senders, self._outbox)
        for strategy_id in self._verifier.due_strategies(mission.name):
            self._verifier.check_strategy(strategy_id)
        self._check_liveness(mission, now)
        if switcher.needs_refill(self._store, self._config, mission.name):
            self._research.refill(mission.name)
        for strategy_id in switcher.pick(self._store, self._config, mission.name):
            self._executor.run_round(strategy_id)
        self._store.write_heartbeat()

    def _load_goal(self, mission):
        path = self._executor.goal_path(mission)
        return parse_goal(path)

    def _check_mission_stop(self, mission, now) -> None:
        goal = self._load_goal(mission)
        stop = brake.mission_verdict(mission, goal, self._store, now)
        if stop is None:
            return
        for strategy in self._store.list_strategies(mission.name):
            if strategy.status in ("dead", "won"):
                continue
            totals = self._store.strategy_spend(strategy.id)
            self._store.close_strategy(
                strategy.id,
                status="dead",
                death_cause="mission_stopped",
                rung_reached="",
                failed_rounds=strategy.failed_rounds,
                money_spent=totals.money,
                tokens=totals.tokens_in + totals.tokens_out,
                hours_to_rung_json="{}",
                closed_at=now.isoformat(),
            )
        self._store.set_mission_status(
            mission.name,
            stop.stop,
            stopped_at=now.isoformat(),
            stop_reason=stop.stop,
        )
        write_report(self._store, mission.name, self._config.paths.missions)
        fan_out(
            Notice(
                kind="mission_stop",
                title=f"mission {mission.name} stopped: {stop.stop}",
                body=(f"settled {self._store.settled_in_target(mission.name)}"),
            ),
            self._notifiers,
            store=self._store,
            mission=mission.name,
        )

    def _collect_replies(self, mission) -> None:
        for notifier in self._notifiers:
            if not getattr(notifier, "supports_replies", False):
                continue
            poll = getattr(notifier, "poll_replies", None)
            if poll is None:
                continue
            for reply in poll():
                try:
                    if reply.command == "approve":
                        self._outbox.approve(
                            reply.outbox_id,
                            reply.token,
                            via=notifier.name,
                            by=str(reply.user_id),
                        )
                    else:
                        self._outbox.reject(
                            reply.outbox_id,
                            via=notifier.name,
                            by=str(reply.user_id),
                        )
                except Exception as error:  # noqa: BLE001 - bad reply warns, never kills tick
                    self._store.add_event(
                        mission=mission.name,
                        level="warn",
                        kind="reply_rejected",
                        message=str(error)[:300],
                    )

    def _fail_rejected_rounds(self, mission) -> None:
        # Decision: a rejected draft fails its round, mirroring expiry.
        # Otherwise a rejected strategy drafts forever without learning.
        for item in self._store.list_outbox("rejected"):
            if item.mission != mission.name:
                continue
            round_row = self._store.round_for_outbox(item.id)
            if round_row is None or round_row.ended_at is not None:
                continue
            self._store.end_round(
                round_row.id,
                ended_at=self._clock.now().isoformat(),
                result_state="fail",
            )

    def _expire_drafts(self, mission) -> None:
        before = {
            item.id
            for item in self._store.list_outbox("awaiting")
            if item.mission == mission.name
        }
        self._outbox.expire_old()
        for item_id in before:
            item = self._store.get_outbox(item_id)
            if item is None or item.status != "expired":
                continue
            round_row = self._store.round_for_outbox(item_id)
            if round_row is None or round_row.ended_at is not None:
                continue
            self._store.end_round(
                round_row.id,
                ended_at=self._clock.now().isoformat(),
                result_state="fail",
            )

    def _check_liveness(self, mission, now) -> None:
        hour_ago = (now - timedelta(hours=1)).isoformat()
        live_channels = set()
        for strategy in self._store.list_strategies(mission.name):
            if strategy.status in ("active", "pending", "queued"):
                live_channels.add(strategy.channel)
        for channel_name in sorted(live_channels):
            if self._liveness_checked.get(channel_name, "") >= hour_ago:
                continue
            self._liveness_checked[channel_name] = now.isoformat()
            channel = self._channels.get(channel_name)
            if channel is None:
                continue
            try:
                live = channel.liveness()
            except Exception:  # noqa: BLE001 - dead channel check means unverified
                live = None
            if live is not None and not live.alive:
                for strategy in self._store.list_strategies(mission.name):
                    if strategy.channel != channel_name:
                        continue
                    if strategy.status in ("dead", "won"):
                        continue
                    totals = self._store.strategy_spend(strategy.id)
                    self._store.close_strategy(
                        strategy.id,
                        status="dead",
                        death_cause="channel_dead",
                        rung_reached="",
                        failed_rounds=strategy.failed_rounds,
                        money_spent=totals.money,
                        tokens=totals.tokens_in + totals.tokens_out,
                        hours_to_rung_json="{}",
                        closed_at=now.isoformat(),
                    )
                self._store.add_event(
                    mission=mission.name,
                    level="warn",
                    kind="channel_dead",
                    message=f"channel {channel_name} failed liveness",
                )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def run(config) -> int:
    from scavenger import research  # noqa: F401  (arrives in Phase 6)

    run_dir = config.paths.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    pid_file = run_dir / "daemon.pid"
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None and _pid_alive(pid):
        return 2
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    try:
        from scavenger import clock
        from scavenger.executor import Executor
        from scavenger.outbox import create_outbox
        from scavenger.verifier import Verifier

        store = Store.open(config.paths.db, run_dir=config.paths.run_dir)
        outbox = create_outbox(store, config, [], clock)
        loop = Loop(
            store,
            config,
            Verifier(store, config, {}, clock),
            Executor(store, config, {}, outbox, clock),
            outbox,
            {},
            {},
            [],
            clock,
        )
        while True:
            loop.tick()
            time.sleep(config.loop.tick_seconds)
    finally:
        try:
            pid_file.unlink()
        except OSError:
            pass
    return 0
