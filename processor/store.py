"""Applies one internal event to the database, exactly once (PRD 9.2).

Everything happens in one transaction:

1. Lock the product's stock_snapshot row (SELECT ... FOR UPDATE). Every other
   worker handling the same product waits here, so no two sales can read the
   same stock at the same time.
2. Work out the event's real effect on stock (see `_effective_units`).
3. INSERT the event with ON CONFLICT DO NOTHING. No row back = duplicate: stop.
4. For a sale without enough free stock, resolve the conflict (conflicts.py) and
   write `cancel` events for the losers plus one `conflicts` row each.
5. Update stock_snapshot so it still equals SUM(events.quantity).

The caller acks the RabbitMQ message only after this function returns, i.e.
after COMMIT. A crash before that means redelivery, and step 3 makes the retry
a no-op.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import psycopg
from psycopg.types.json import Jsonb

from conflicts import Candidate, ScoringConfig, explain, resolve

log = logging.getLogger("processor.store")


class UnknownProduct(ValueError):
    """The SKU is not in `products`; the message goes to the dead-letter queue."""


@dataclass
class Outcome:
    status: str                      # applied | duplicate | conflict | noop
    product_id: int | None = None
    sku: str | None = None
    event_id: int | None = None
    on_hand: int | None = None
    conflict_ids: list[int] = field(default_factory=list)
    note: str | None = None


def process_event(conn: psycopg.Connection, evt: dict, cfg: ScoringConfig) -> Outcome:
    with conn.transaction(), conn.cursor() as cur:
        product_id, cost_price = _product(cur, evt["sku"])
        on_hand, reserved = _lock_stock(cur, product_id)

        if _already_recorded(cur, evt):
            return Outcome("duplicate", product_id, evt["sku"], on_hand=on_hand)

        customer_id, lifetime_value = _customer(cur, evt)
        units, note = _effective_units(cur, evt, product_id)
        signed = -units if evt["type"] == "sale" else units

        event_id = _insert_event(cur, evt, product_id, customer_id, signed, note)
        if event_id is None:  # lost a race with an identical message; harmless
            return Outcome("duplicate", product_id, evt["sku"], on_hand=on_hand)

        if evt["type"] != "sale" or units == 0:
            on_hand = _update_stock(cur, product_id, signed, event_id)
            status = "noop" if units == 0 else "applied"
            return Outcome(status, product_id, evt["sku"], event_id, on_hand, note=note)

        available = on_hand - _reserved_for_others(cur, product_id, evt["channel"], on_hand)
        if available >= units:
            on_hand = _update_stock(cur, product_id, -units, event_id)
            return Outcome("applied", product_id, evt["sku"], event_id, on_hand)

        # Not enough free stock: conflict.
        incoming = Candidate(
            event_id=event_id,
            channel=evt["channel"],
            order_id=evt.get("order_id"),
            units=units,
            unit_price=float(evt.get("unit_price") or 0),
            cost_price=float(cost_price),
            lifetime_value=float(lifetime_value),
            cancel_penalty=_cancel_penalty(cur, evt["channel"]),
            occurred_at=_parse_time(evt["occurred_at"]),
        )
        competitors = _competing_sales(cur, product_id, event_id, float(cost_price), cfg.conflict_window_sec)
        res = resolve(incoming, competitors, available, cfg)

        delta = -units if res.incoming_wins else 0
        last_id = event_id
        conflict_ids = []
        for loser in res.losers:
            cancel_id = _write_cancel(cur, product_id, loser, caused_by=res.winner.event_id if res.winner else None)
            last_id = cancel_id
            if res.incoming_wins:
                delta += loser.units
            reason = explain(evt["sku"], available, incoming, res, loser)
            cur.execute(
                """INSERT INTO conflicts (product_id, winner_event, loser_event, scores, reason)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (product_id, res.winner.event_id if res.winner else None, loser.event_id,
                 Jsonb({"available": available, "candidates": res.scores}), reason),
            )
            conflict_ids.append(cur.fetchone()[0])
            log.info("conflict %s", reason)

        on_hand = _update_stock(cur, product_id, delta, last_id)
        return Outcome("conflict", product_id, evt["sku"], event_id, on_hand, conflict_ids)


# ---------------------------------------------------------------------------
# helpers (all run inside the caller's transaction)
# ---------------------------------------------------------------------------

def _product(cur, sku: str) -> tuple[int, float]:
    cur.execute("SELECT id, cost_price FROM products WHERE sku = %s", (sku,))
    row = cur.fetchone()
    if row is None:
        raise UnknownProduct(f"unknown sku {sku!r}")
    return row[0], row[1]


def _lock_stock(cur, product_id: int) -> tuple[int, int]:
    # A product added after seeding may have no snapshot row yet.
    cur.execute(
        "INSERT INTO stock_snapshot (product_id, on_hand) VALUES (%s, 0) ON CONFLICT DO NOTHING",
        (product_id,),
    )
    cur.execute(
        "SELECT on_hand, reserved FROM stock_snapshot WHERE product_id = %s FOR UPDATE",
        (product_id,),
    )
    return cur.fetchone()


def _already_recorded(cur, evt: dict) -> bool:
    cur.execute(
        "SELECT 1 FROM events WHERE channel = %s AND external_event_id = %s",
        (evt["channel"], evt["external_event_id"]),
    )
    return cur.fetchone() is not None


def _customer(cur, evt: dict) -> tuple[int | None, float]:
    ref = evt.get("customer_ref")
    if not ref:
        return None, 0.0
    cur.execute(
        """INSERT INTO customers (channel, external_id) VALUES (%s, %s)
           ON CONFLICT (channel, external_id) DO UPDATE SET external_id = EXCLUDED.external_id
           RETURNING id, lifetime_value""",
        (evt["channel"], ref),
    )
    cid, ltv = cur.fetchone()
    return cid, float(ltv or 0)


def _effective_units(cur, evt: dict, product_id: int) -> tuple[int, str | None]:
    """How many units this event really moves, given what we already know about the order.

    Delivery is out of order and channels may cancel orders we already cancelled, so:
    - sale:          0 if the order was already cancelled (the cancel arrived first)
    - cancel/return: at most the units the order still holds; 0 if none
    The stored `quantity` is this effective value, so SUM(events) stays equal to stock.
    """
    requested = abs(int(evt["quantity"]))
    order_id = evt.get("order_id")
    if not order_id:
        return requested, None

    if evt["type"] == "sale":
        cur.execute(
            """SELECT 1 FROM events WHERE product_id = %s AND channel = %s AND order_id = %s
               AND type = 'cancel' LIMIT 1""",
            (product_id, evt["channel"], order_id),
        )
        if cur.fetchone():
            return 0, "order already cancelled before the sale arrived"
        return requested, None

    cur.execute(
        """SELECT COALESCE(-SUM(quantity), 0) FROM events
           WHERE product_id = %s AND channel = %s AND order_id = %s""",
        (product_id, evt["channel"], order_id),
    )
    outstanding = max(int(cur.fetchone()[0]), 0)
    if outstanding >= requested:
        return requested, None
    if outstanding == 0:
        return 0, f"no units outstanding on order {order_id}; {evt['type']} has no stock effect"
    return outstanding, f"only {outstanding} of {requested} units outstanding on order {order_id}"


def _insert_event(cur, evt, product_id, customer_id, signed_qty, note) -> int | None:
    payload = dict(evt)
    payload["requested_quantity"] = evt["quantity"]
    if note:
        payload["note"] = note
    cur.execute(
        """INSERT INTO events (external_event_id, channel, product_id, type, quantity, unit_price,
                               order_id, customer_id, occurred_at, payload)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT ON CONSTRAINT events_channel_external_event_id_key DO NOTHING
           RETURNING id""",
        (evt["external_event_id"], evt["channel"], product_id, evt["type"], signed_qty,
         evt.get("unit_price"), evt.get("order_id"), customer_id, evt["occurred_at"], Jsonb(payload)),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _update_stock(cur, product_id: int, delta: int, last_event_id: int) -> int:
    cur.execute(
        """UPDATE stock_snapshot SET on_hand = on_hand + %s, last_event_id = %s, updated_at = now()
           WHERE product_id = %s RETURNING on_hand""",
        (delta, last_event_id, product_id),
    )
    return cur.fetchone()[0]


def _reserved_for_others(cur, product_id: int, channel: str, on_hand: int) -> int:
    """Units held back for other channels by approved allocation rules (PRD M7)."""
    cur.execute(
        """SELECT COALESCE(SUM(units), 0) FROM allocation_rules
           WHERE product_id = %s AND kind = 'reserve' AND active AND channel <> %s""",
        (product_id, channel),
    )
    return min(int(cur.fetchone()[0]), max(on_hand, 0))


def _cancel_penalty(cur, channel: str) -> float:
    cur.execute("SELECT cancel_penalty FROM channels WHERE code = %s", (channel,))
    row = cur.fetchone()
    return float(row[0]) if row else 0.0


def _competing_sales(cur, product_id: int, exclude_id: int, cost_price: float, window_sec: float) -> list[Candidate]:
    """Sales of this product in the conflict window that still hold stock."""
    cur.execute(
        """SELECT e.id, e.channel, e.order_id, -e.quantity, COALESCE(e.unit_price, 0),
                  COALESCE(c.lifetime_value, 0), ch.cancel_penalty, e.occurred_at
           FROM events e
           JOIN channels ch ON ch.code = e.channel
           LEFT JOIN customers c ON c.id = e.customer_id
           WHERE e.product_id = %s AND e.type = 'sale' AND e.quantity < 0 AND e.id <> %s
             AND e.recorded_at >= now() - make_interval(secs => %s)
             AND NOT EXISTS (
               SELECT 1 FROM events x
               WHERE x.product_id = e.product_id AND x.channel = e.channel
                 AND x.order_id = e.order_id AND x.type IN ('cancel', 'return'))""",
        (product_id, exclude_id, window_sec),
    )
    return [
        Candidate(
            event_id=r[0], channel=r[1], order_id=r[2], units=int(r[3]), unit_price=float(r[4]),
            cost_price=cost_price, lifetime_value=float(r[5]), cancel_penalty=float(r[6]), occurred_at=r[7],
        )
        for r in cur.fetchall()
    ]


def _write_cancel(cur, product_id: int, loser: Candidate, caused_by: int | None) -> int:
    cur.execute(
        """INSERT INTO events (external_event_id, channel, product_id, type, quantity, unit_price,
                               order_id, caused_by, occurred_at, payload)
           VALUES (%s, %s, %s, 'cancel', %s, NULL, %s, %s, now(), %s)
           RETURNING id""",
        (f"stocksense-cancel-{loser.event_id}", loser.channel, product_id, loser.units, loser.order_id,
         caused_by, Jsonb({"source": "conflict_resolution", "cancelled_event": loser.event_id})),
    )
    return cur.fetchone()[0]


def _parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))
