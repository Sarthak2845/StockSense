from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg import Connection

from db import get_conn

router = APIRouter(prefix="/products", tags=["products"])


@router.get("")
def list_products(conn: Connection = Depends(get_conn)) -> list[dict]:
    """Products with current stock (FR-6.1)."""
    return conn.execute(
        """SELECT p.id, p.sku, p.name, p.cost_price, p.low_stock_at,
                  COALESCE(s.on_hand, 0) AS on_hand,
                  COALESCE(s.reserved, 0) AS reserved,
                  COALESCE(s.on_hand, 0) - COALESCE(s.reserved, 0) AS available,
                  COALESCE(s.on_hand, 0) <= p.low_stock_at AS low_stock,
                  s.updated_at
           FROM products p LEFT JOIN stock_snapshot s ON s.product_id = p.id
           ORDER BY p.sku"""
    ).fetchall()


def _require_product(conn: Connection, product_id: int) -> dict:
    row = conn.execute("SELECT id, sku, name FROM products WHERE id = %s", (product_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="product not found")
    return row


@router.get("/{product_id}/events")
def product_events(
    product_id: int,
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(200, ge=1, le=1000),
    conn: Connection = Depends(get_conn),
) -> dict:
    """Event timeline for one product, newest first (FR-6.3)."""
    product = _require_product(conn, product_id)
    events = conn.execute(
        """SELECT e.id, e.external_event_id, e.channel, e.type, e.quantity, e.unit_price,
                  e.order_id, cu.external_id AS customer_ref, e.caused_by,
                  e.occurred_at, e.recorded_at, e.payload->>'note' AS note
           FROM events e LEFT JOIN customers cu ON cu.id = e.customer_id
           WHERE e.product_id = %s
             AND (%s::timestamptz IS NULL OR e.recorded_at >= %s::timestamptz)
             AND (%s::timestamptz IS NULL OR e.recorded_at <= %s::timestamptz)
           ORDER BY e.recorded_at DESC, e.id DESC
           LIMIT %s""",
        (product_id, from_, from_, to, to, limit),
    ).fetchall()
    return {"product": product, "events": events}


@router.get("/{product_id}/stock")
def stock_at(
    product_id: int,
    at: datetime | None = None,
    conn: Connection = Depends(get_conn),
) -> dict:
    """Time travel (PRD 9.4): stock at any past moment = SUM(quantity) of events up to then."""
    product = _require_product(conn, product_id)
    row = conn.execute(
        """SELECT COALESCE(SUM(quantity), 0) AS stock, count(*) AS events_counted
           FROM events
           WHERE product_id = %s AND (%s::timestamptz IS NULL OR recorded_at <= %s::timestamptz)""",
        (product_id, at, at),
    ).fetchone()
    return {"product": product, "at": at, **row}
