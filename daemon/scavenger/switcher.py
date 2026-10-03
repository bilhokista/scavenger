from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class RungPlan:
    adapter: str
    locator: dict
    max_rounds: int = 3
    max_pending_hours: float | None = None


@dataclass(frozen=True)
class Candidate:
    channel: str
    path: str
    path_key: str
    outward_key: str
    payout_amount: Decimal
    payout_currency: str
    guarantor: str
    guarantor_evidence: str | None
    capital_needed: Decimal
    hours_first_proof: float
    hours_settlement: float
    probability: float
    probability_note: str
    source_urls: tuple = ()
    rungs: tuple = ()


@dataclass(frozen=True)
class RankedCandidate:
    candidate: Candidate
    score: Decimal
    expected: Decimal
    probability: float
    probability_source: str


def _laplace_probability(store, channel: str, minimum: int):
    rows = store.ledger_for_channel(channel)
    if len(rows) < minimum:
        return None
    won = sum(1 for row in rows if row.outcome == "won")
    return (won + 1) / (len(rows) + 2)


def rank(candidates, goal, store, config, now: datetime) -> list:
    settled = store.settled_in_target(goal.name)
    gap = goal.target.amount - settled
    ladder_length = max(len(goal.ladder), 1)
    ranked = []
    for candidate in candidates:
        if candidate.payout_currency.upper() == goal.target.currency.upper():
            payout_target = candidate.payout_amount
        else:
            rate = config.fx.rates.get(
                (
                    candidate.payout_currency.upper(),
                    goal.target.currency.upper(),
                )
            )
            if rate is None:
                store.add_event(
                    mission=goal.name,
                    level="info",
                    kind="candidate_dropped",
                    message=f"{candidate.path_key}: no fx rate",
                )
                continue
            payout_target = candidate.payout_amount * rate
        eta = now + timedelta(hours=candidate.hours_settlement)
        if eta > goal.target.deadline:
            store.add_event(
                mission=goal.name,
                level="info",
                kind="candidate_dropped",
                message=f"{candidate.path_key}: cannot settle before deadline",
            )
            continue
        if payout_target < gap * config.research.gap_floor_ratio:
            store.add_event(
                mission=goal.name,
                level="info",
                kind="candidate_dropped",
                message=f"{candidate.path_key}: below gap floor",
            )
            continue
        laplace = _laplace_probability(
            store,
            candidate.channel,
            config.ledger.min_history_for_probability,
        )
        if laplace is None:
            probability = min(max(candidate.probability, 0.01), 0.90)
            source = "estimate"
        else:
            probability = laplace
            source = "ledger"
        expected = payout_target * Decimal(str(probability))
        blended = (
            config.llm.price_per_million_input + config.llm.price_per_million_output
        ) / 2
        tokens = Decimal(50000 * ladder_length)
        est_cost = candidate.capital_needed + tokens * blended / 1000000
        score = expected / max(est_cost, Decimal("0.01"))
        ranked.append(
            RankedCandidate(
                candidate=candidate,
                score=score,
                expected=expected,
                probability=probability,
                probability_source=source,
            )
        )
    ranked.sort(
        key=lambda item: (
            item.candidate.guarantor == "none",
            -item.score,
            item.candidate.hours_first_proof,
            item.candidate.capital_needed,
            item.candidate.path_key,
        )
    )
    return ranked


def pick(store, config, mission: str) -> list:
    # Decision: plan fixes pick(mission), which cannot work without store
    # and config access, so both are explicit parameters.
    active = store.list_strategies(mission, ("active",))
    used_keys = {strategy.outward_key for strategy in active}
    room = config.loop.max_active - len(active)
    picked = []
    if room <= 0:
        return picked
    for strategy in store.list_strategies(mission, ("queued",)):
        if strategy.outward_key in used_keys:
            continue
        store.update_strategy(strategy.id, status="active")
        used_keys.add(strategy.outward_key)
        picked.append(strategy.id)
        if len(picked) >= room:
            break
    return picked


def needs_refill(store, config, mission: str) -> bool:
    live = store.list_strategies(
        mission, ("queued", "active", "pending", "awaiting_approval")
    )
    if live:
        return False
    record = store.get_mission(mission)
    if record.last_research_at is None:
        return True
    interval = timedelta(minutes=config.research.min_interval_minutes)
    return datetime.fromisoformat(record.last_research_at) + interval <= datetime.now(
        UTC
    )
