import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from scavenger import brake
from scavenger.config import ConfigError
from scavenger.goal import parse as parse_goal
from scavenger.outbox import DraftSpec


@dataclass(frozen=True)
class AgentResult:
    ok: bool
    patch_path: str | None
    description_path: str | None
    detail: str


class Executor:
    def __init__(self, store, config, channels: dict, outbox, clock) -> None:
        self._store = store
        self._config = config
        self._channels = channels
        self._outbox = outbox
        self._clock = clock

    def run_round(self, strategy_id: int) -> str:
        store = self._store
        strategy = store.get_strategy(strategy_id)
        goal = parse_goal(self.goal_path(store.get_mission(strategy.mission)))
        verdict = brake.strategy_verdict(strategy, goal)
        if verdict is not None:
            self._close(strategy, verdict.cause)
            return f"dead:{verdict.cause}"
        for item in store.list_outbox("awaiting") + store.list_outbox("approved"):
            if item.strategy_id == strategy_id:
                return "skipped:open-outbox"
        channel = self._channels[strategy.channel]
        checks = store.last_proof_checks(strategy_id, 1)
        action = channel.next_action(strategy, checks[0] if checks else None)
        round_id = store.start_round(
            strategy_id,
            started_at=self._clock.now().isoformat(),
            action_kind=action.kind,
            action_fingerprint=action.fingerprint,
        )
        if action.tokens_in + action.tokens_out > 0:
            store.add_spend(
                mission=strategy.mission,
                strategy_id=strategy_id,
                at=self._clock.now().isoformat(),
                tokens_in=action.tokens_in,
                tokens_out=action.tokens_out,
                money=Decimal(0),
                source="llm",
            )
        if action.kind == "draft":
            item = self._outbox.create_draft(
                action.draft, strategy.mission, strategy_id
            )
            store.set_round_outbox(round_id, item.id)
            return "drafted"
        if action.kind == "local":
            return self._run_local(strategy, round_id, action)
        store.end_round(
            round_id,
            ended_at=self._clock.now().isoformat(),
            result_state="pending",
        )
        return "waited"

    def _run_local(self, strategy, round_id: int, action) -> str:
        repo = self._pr_repo(strategy)
        result = self.run_agent(strategy, action.description)
        if not result.ok:
            self._store.add_proof_check(
                strategy.id,
                round_id,
                self._rung_name(strategy),
                "agent",
                "{}",
                "fail",
                result.detail[-65536:],
                None,
                self._clock.now().isoformat(),
            )
            self._store.end_round(
                round_id,
                ended_at=self._clock.now().isoformat(),
                result_state="fail",
            )
            return "agent-failed"
        description = ""
        if result.description_path is not None:
            description = Path(result.description_path).read_text(encoding="utf-8")
        title = (
            description.strip().splitlines()[0]
            if description.strip()
            else (f"scavenger work for {strategy.path_key}")
        )
        branch = f"scav-{strategy.id}-{round_id}"
        item = self._outbox.create_draft(
            DraftSpec(
                kind="github_pr",
                target=repo,
                body=description,
                payload_json=json.dumps(
                    {
                        "repo": repo,
                        "base": "main",
                        "branch": branch,
                        "title": title,
                        "patch_path": result.patch_path,
                    }
                ),
            ),
            strategy.mission,
            strategy.id,
        )
        self._store.set_round_outbox(round_id, item.id)
        return "agent-drafted"

    def _pr_repo(self, strategy) -> str:
        if not strategy.outward_key.startswith("github:"):
            raise ConfigError(
                f"local action needs a github outward_key, got {strategy.outward_key!r}"
            )
        return strategy.outward_key[len("github:") :]

    def _rung_name(self, strategy) -> str:
        try:
            rungs = json.loads(strategy.rungs_json)
            return rungs[strategy.rung_index].get("rung", "")
        except (ValueError, IndexError, AttributeError):
            return ""

    def run_agent(self, strategy, instructions: str) -> AgentResult:
        command = list(self._config.executor.agent_command)
        if not command:
            raise ConfigError(
                "channel produced a local action but executor.agent_command is empty"
            )
        workdir = (
            self._config.paths.missions / strategy.mission / "work" / str(strategy.id)
        )
        workdir.mkdir(parents=True, exist_ok=True)
        prompt = (
            f"{instructions}\n\nRules: do the work in this directory."
            " Write the unified patch to out/patch.diff and a PR"
            " description to out/pr.md. Never push, never send anything,"
            " never touch the network."
        )
        timeout = self._config.executor.agent_timeout_minutes * 60
        try:
            completed = subprocess.run(  # noqa: PLW1510 - returncode handled below
                [*command, prompt],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            return AgentResult(
                ok=False,
                patch_path=None,
                description_path=None,
                detail=f"agent timed out: {error}",
            )
        if completed.returncode != 0:
            return AgentResult(
                ok=False,
                patch_path=None,
                description_path=None,
                detail=(completed.stdout + completed.stderr)[-65536:],
            )
        patch = workdir / "out" / "patch.diff"
        if not patch.exists() or not patch.read_text(encoding="utf-8").strip():
            return AgentResult(
                ok=False,
                patch_path=None,
                description_path=None,
                detail=(completed.stdout + completed.stderr)[-65536:]
                or "agent wrote no patch",
            )
        description = workdir / "out" / "pr.md"
        return AgentResult(
            ok=True,
            patch_path=str(patch),
            description_path=(str(description) if description.exists() else None),
            detail="patch ready",
        )

    def goal_path(self, mission) -> Path:
        path = Path(mission.goal_path)
        if path.is_absolute():
            return path
        return self._config.paths.missions.parent / path

    def _close(self, strategy, cause: str) -> None:
        totals = self._store.strategy_spend(strategy.id)
        try:
            rungs = json.loads(strategy.rungs_json)
            rung = rungs[strategy.rung_index].get("rung", "")
        except (ValueError, IndexError, AttributeError):
            rung = ""
        self._store.close_strategy(
            strategy.id,
            status="dead",
            death_cause=cause,
            rung_reached=rung,
            failed_rounds=strategy.failed_rounds,
            money_spent=totals.money,
            tokens=totals.tokens_in + totals.tokens_out,
            hours_to_rung_json="{}",
            closed_at=self._clock.now().isoformat(),
        )
