from decimal import Decimal
from pathlib import Path

from scavenger.goal import parse as parse_goal


def render_report(store, mission_name: str) -> str:
    mission = store.get_mission(mission_name)
    settled = store.settled_in_target(mission_name)
    target = Decimal(mission.target_amount)
    percentage = (
        (settled / target * 100).quantize(Decimal("0.1")) if target > 0 else Decimal(0)
    )
    lines = [
        "# Mission report",
        "",
        "## Outcome",
        "",
        f"Status: {mission.status}",
        f"Stop reason: {mission.stop_reason or '-'}",
        f"Settled: {settled} / {target} {mission.target_currency} ({percentage}%)",
    ]
    if settled == 0:
        lines.append("Verdict: FAILED - activity without settled money.")
    else:
        lines.append(f"Verdict: {mission.status}.")
    lines += ["", "## Strategies", ""]
    strategies = store.list_strategies(mission_name)
    if not strategies:
        lines.append("No strategies.")
    for strategy in strategies:
        checks = store.last_proof_checks(strategy.id, 1)
        evidence = checks[0].id if checks else "-"
        lines.append(
            f"- #{strategy.id} {strategy.channel} | {strategy.path} |"
            f" {strategy.status} | rung {strategy.rung_index} |"
            f" failed {strategy.failed_rounds} | loss {strategy.loss} |"
            f" {strategy.death_cause or '-'} | evidence {evidence}"
        )
    lines += ["", "## Spend", ""]
    total = store.spend_since(mission_name, "0001-01-01T00:00:00+00:00")
    lines.append(
        f"Money: {total.money} | tokens in: {total.tokens_in} |"
        f" tokens out: {total.tokens_out}"
    )
    lines += ["", "## Settlements", ""]
    settlements = store.list_settlements(mission_name)
    if not settlements:
        lines.append("None.")
    for settlement in settlements:
        lines.append(
            f"- {settlement.amount} {settlement.currency}"
            f" {settlement.message_id} at {settlement.at}"
        )
    lines += ["", "## Ledger lessons", ""]
    blocks = store.list_blocks()
    if blocks:
        for block in blocks:
            lines.append(
                f"- blocked {block.channel}: {block.cause} until {block.until}"
            )
    else:
        lines.append("No channel blocks.")
    lines += ["", "## Assumptions", ""]
    goal_file = Path(mission.goal_path)
    if goal_file.exists():
        goal = parse_goal(goal_file)
        if goal.assumptions:
            for assumption in goal.assumptions:
                lines.append(f"- [{assumption.source}] {assumption.text}")
        else:
            lines.append("None recorded.")
    else:
        lines.append("GOAL.md not found.")
    return "\n".join(lines) + "\n"


def write_report(store, mission_name: str, missions_dir) -> Path:
    path = Path(missions_dir) / mission_name / "REPORT.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(store, mission_name), encoding="utf-8")
    return path
