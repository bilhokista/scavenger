import json
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from scavenger import switcher
from scavenger.outbox import DraftSpec

_PROBABILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "probabilities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path_key": {"type": "string"},
                    "probability": {"type": "number"},
                    "note": {"type": "string"},
                },
            },
        }
    },
}


class Research:
    def __init__(self, store, config, channels: dict, llm, outbox, clock) -> None:
        self._store = store
        self._config = config
        self._channels = channels
        self._llm = llm
        self._outbox = outbox
        self._clock = clock

    def refill(self, mission: str, goal=None) -> None:
        store = self._store
        self._update_blocks()
        if goal is None:
            goal = self._load_goal(mission)
        seen = {strategy.path_key for strategy in store.list_strategies(mission)}
        fresh = []
        for name, channel in self._channels.items():
            if store.active_block(name) is not None:
                continue
            for candidate in channel.discover(goal):
                if candidate.path_key in seen:
                    continue
                seen.add(candidate.path_key)
                fresh.append(candidate)
        scored = self._fill_probabilities(fresh, mission)
        ranked = switcher.rank(scored, goal, store, self._config, self._clock.now())
        kept = ranked[: self._config.research.max_strategies]
        for item in kept:
            self._insert(item, mission)
        if kept:
            self._store.reset_empty_refills(mission)
        else:
            self._store.note_empty_refill(mission)
        self._store.note_research(mission, self._clock.now().isoformat())

    def _update_blocks(self) -> None:
        window = self._config.ledger.block_days
        after = self._config.ledger.block_after_same_cause_deaths
        now = self._clock.now()
        for channel in self._channels:
            causes = Counter()
            for row in self._store.ledger_for_channel(channel):
                if row.outcome != "dead" or not row.death_cause:
                    continue
                try:
                    closed = datetime.fromisoformat(row.closed_at)
                except ValueError:
                    continue
                if closed >= now - timedelta(days=window):
                    causes[row.death_cause] += 1
            for cause, count in causes.items():
                if count >= after:
                    self._store.block_channel(
                        channel,
                        cause,
                        (now + timedelta(days=window)).isoformat(),
                        created_at=now.isoformat(),
                    )

    def _fill_probabilities(self, candidates: list, mission: str) -> list:
        with_history = []
        without_history = []
        for candidate in candidates:
            if self._store.ledger_for_channel(candidate.channel):
                with_history.append(candidate)
            else:
                without_history.append(candidate)
        if not without_history:
            return candidates
        scored = {candidate.path_key: candidate for candidate in with_history}
        spent_tokens = 0
        for index in range(0, len(without_history), 20):
            batch = without_history[index : index + 20]
            if spent_tokens > self._config.research.max_tokens_per_run:
                break
            table = "\n".join(
                f"- {item.path_key}: {item.path} "
                f"(pays {item.payout_amount} {item.payout_currency})"
                for item in batch
            )
            estimates = self._estimate_batch(table, mission)
            spent_tokens += estimates[1]
            for item in batch:
                estimate = estimates[0].get(item.path_key)
                if estimate is None:
                    scored[item.path_key] = item
                else:
                    scored[item.path_key] = replace(
                        item,
                        probability=estimate.get("probability", item.probability),
                        probability_note=estimate.get("note", ""),
                    )
        return [scored[key] for key in scored]

    def _estimate_batch(self, table: str, mission: str) -> tuple:
        # Same one-retry rule as llm.complete_json, but research also
        # needs the token counts for the run cap and spend rows.
        system = "Estimate success probability per opportunity."
        prompt = f"Opportunities:\n{table}\nReply JSON only."
        spent = 0
        for attempt in range(2):
            result = self._llm.complete(system, prompt, json_schema=_PROBABILITY_SCHEMA)
            spent += result.tokens_in + result.tokens_out
            self._store.add_spend(
                mission=mission,
                strategy_id=None,
                at=self._clock.now().isoformat(),
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                money=Decimal(0),
                source="llm",
            )
            try:
                return (
                    {
                        entry["path_key"]: entry
                        for entry in json.loads(result.text)["probabilities"]
                    },
                    spent,
                )
            except (ValueError, KeyError):
                continue
        return {}, spent

    def _insert(self, ranked, mission: str) -> None:
        candidate = ranked.candidate
        strategy_id = self._store.add_strategy(
            mission=mission,
            channel=candidate.channel,
            path=candidate.path,
            path_key=candidate.path_key,
            outward_key=candidate.outward_key,
            payout_amount=candidate.payout_amount,
            payout_currency=candidate.payout_currency,
            guarantor=candidate.guarantor,
            guarantor_evidence=candidate.guarantor_evidence,
            capital_needed=candidate.capital_needed,
            hours_first_proof=candidate.hours_first_proof,
            hours_settlement=candidate.hours_settlement,
            probability=ranked.probability,
            probability_source=ranked.probability_source,
            probability_note=candidate.probability_note,
            score=ranked.score,
            rungs_json=json.dumps(
                [
                    dict(rung)
                    if isinstance(rung, dict)
                    else {
                        "rung": rung.rung,
                        "adapter": rung.adapter,
                        "locator": rung.locator,
                    }
                    for rung in candidate.rungs
                ]
            ),
            status=("awaiting_approval" if candidate.guarantor == "none" else "queued"),
            created_at=self._clock.now().isoformat(),
        )
        if candidate.guarantor == "none":
            self._outbox.create_draft(
                DraftSpec(
                    kind="strategy_start",
                    target=candidate.path,
                    body=(
                        f"Strategy {candidate.path_key} pays"
                        f" {candidate.payout_amount}"
                        f" {candidate.payout_currency} with no guarantor."
                        f" Evidence: {candidate.guarantor_evidence}."
                        " Approve to queue it."
                    ),
                ),
                mission,
                strategy_id,
            )

    def _load_goal(self, mission: str):
        from scavenger.goal import parse as parse_goal

        record = self._store.get_mission(mission)
        path = Path(record.goal_path)
        if not path.is_absolute():
            path = self._config.paths.missions.parent / path
        return parse_goal(path)
