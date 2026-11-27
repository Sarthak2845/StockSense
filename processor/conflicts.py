"""Conflict scoring and resolution (PRD 9.3). Pure functions, no database.

When a sale arrives and there is not enough free stock, every sale for the same
product inside the conflict window competes. Each gets a score:

    score = w1*margin + w2*customerValue + w3*cancelPenalty - w4*lateness

- margin         (unit price - cost price) * units, in Rs
- customerValue  lifetime value scaled to 0..100
- cancelPenalty  what cancelling an order on that channel costs the seller
- lateness       ms after the earliest competing order (tie-breaker: earlier wins)

The incoming sale may take stock from already-applied sales only if the combined
score of the orders it would cancel is lower than its own score. Otherwise the
incoming sale is the one cancelled.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ScoringConfig:
    w_margin: float = 1.0
    w_customer_value: float = 0.5
    w_cancel_penalty: float = 1.0
    w_lateness: float = 0.01
    customer_value_cap: float = 5000.0
    conflict_window_sec: float = 2.0

    @classmethod
    def load(cls, path: str | Path | None = None) -> "ScoringConfig":
        path = Path(path) if path else Path(__file__).with_name("scoring.json")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: float(v) for k, v in raw.items() if not k.startswith("_")})


@dataclass(frozen=True)
class Candidate:
    """One sale competing for stock."""

    event_id: int
    channel: str
    order_id: str | None
    units: int
    unit_price: float
    cost_price: float
    lifetime_value: float
    cancel_penalty: float
    occurred_at: datetime


@dataclass(frozen=True)
class Resolution:
    incoming_wins: bool
    losers: list[Candidate]
    winner: Candidate | None          # the order that kept the stock (for the explanation)
    scores: list[dict]                # one breakdown per candidate, for conflicts.scores


def customer_value(lifetime_value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return round(min(max(lifetime_value, 0.0) / cap * 100.0, 100.0), 2)


def score_breakdown(c: Candidate, first_at: datetime, cfg: ScoringConfig) -> dict:
    margin = round((c.unit_price - c.cost_price) * c.units, 2)
    cv = customer_value(c.lifetime_value, cfg.customer_value_cap)
    lateness_ms = max((c.occurred_at - first_at).total_seconds() * 1000.0, 0.0)
    total = (
        cfg.w_margin * margin
        + cfg.w_customer_value * cv
        + cfg.w_cancel_penalty * c.cancel_penalty
        - cfg.w_lateness * lateness_ms
    )
    return {
        "event_id": c.event_id,
        "channel": c.channel,
        "order_id": c.order_id,
        "units": c.units,
        "unit_price": c.unit_price,
        "cost_price": c.cost_price,
        "margin": margin,
        "lifetime_value": c.lifetime_value,
        "customer_value": cv,
        "cancel_penalty": c.cancel_penalty,
        "lateness_ms": round(lateness_ms, 1),
        "score": round(total, 2),
        "weights": {
            "margin": cfg.w_margin,
            "customer_value": cfg.w_customer_value,
            "cancel_penalty": cfg.w_cancel_penalty,
            "lateness": cfg.w_lateness,
        },
    }


def resolve(incoming: Candidate, applied: list[Candidate], available: int, cfg: ScoringConfig) -> Resolution:
    """Decide who keeps the stock when `incoming` needs more units than `available`.

    `applied` are sales in the conflict window that currently hold stock.
    """
    everyone = [incoming, *applied]
    first_at = min(c.occurred_at for c in everyone)
    breakdown = {c.event_id: score_breakdown(c, first_at, cfg) for c in everyone}
    score = {eid: b["score"] for eid, b in breakdown.items()}

    needed = incoming.units - max(available, 0)

    # Cheapest orders first; stop as soon as enough units are freed.
    to_cancel: list[Candidate] = []
    freed = 0
    for c in sorted(applied, key=lambda c: (score[c.event_id], -c.event_id)):
        if freed >= needed:
            break
        to_cancel.append(c)
        freed += c.units

    incoming_wins = (
        freed >= needed
        and len(to_cancel) > 0
        and sum(score[c.event_id] for c in to_cancel) < score[incoming.event_id]
    )

    if incoming_wins:
        losers, winner = to_cancel, incoming
    else:
        losers = [incoming]
        winner = max(applied, key=lambda c: (score[c.event_id], -c.event_id)) if applied else None

    loser_ids = {c.event_id for c in losers}
    scores = []
    for c in everyone:
        b = dict(breakdown[c.event_id])
        b["outcome"] = "cancelled" if c.event_id in loser_ids else "kept"
        b["incoming"] = c.event_id == incoming.event_id
        scores.append(b)
    scores.sort(key=lambda b: -b["score"])

    return Resolution(incoming_wins=incoming_wins, losers=losers, winner=winner, scores=scores)


def explain(sku: str, available: int, incoming: Candidate, res: Resolution, loser: Candidate) -> str:
    """One plain-English sentence per cancelled order, stored in conflicts.reason."""
    by_id = {s["event_id"]: s for s in res.scores}
    lost = by_id[loser.event_id]
    if res.winner is None:
        return (
            f"{sku}: out of stock ({available} free). Cancelled {loser.channel} order "
            f"{loser.order_id} (score {lost['score']}); no other order in the conflict window."
        )
    kept = by_id[res.winner.event_id]
    why = []
    if kept["margin"] > lost["margin"]:
        why.append("higher margin")
    if kept["cancel_penalty"] > lost["cancel_penalty"]:
        why.append(f"higher cancellation penalty on {res.winner.channel}")
    if kept["customer_value"] > lost["customer_value"]:
        why.append("more valuable customer")
    if kept["lateness_ms"] < lost["lateness_ms"] and not why:
        why.append("arrived first")
    reason = ", ".join(why) or "higher total score"
    rule = (
        "the incoming order outscored an order already holding the stock"
        if res.incoming_wins
        else "the stock was already held by a higher-scoring order"
    )
    return (
        f"{sku}: {available} free, kept {res.winner.channel} order {res.winner.order_id} "
        f"(score {kept['score']}), cancelled {loser.channel} order {loser.order_id} "
        f"(score {lost['score']}): {reason}; {rule}."
    )
