from fastapi import APIRouter, Depends, Query
from psycopg import Connection

from db import get_conn

router = APIRouter(prefix="/conflicts", tags=["conflicts"])


@router.get("")
def list_conflicts(
    limit: int = Query(50, ge=1, le=500),
    product_id: int | None = None,
    conn: Connection = Depends(get_conn),
) -> list[dict]:
    """Conflict list: winner, loser, every score and the reason (FR-6.2)."""
    return conn.execute(
        """SELECT c.id, c.created_at, p.sku, c.reason, c.scores,
                  w.id AS winner_event, w.channel AS winner_channel, w.order_id AS winner_order,
                  l.id AS loser_event, l.channel AS loser_channel, l.order_id AS loser_order
           FROM conflicts c
           JOIN products p ON p.id = c.product_id
           LEFT JOIN events w ON w.id = c.winner_event
           LEFT JOIN events l ON l.id = c.loser_event
           WHERE %s::int IS NULL OR c.product_id = %s::int
           ORDER BY c.created_at DESC, c.id DESC
           LIMIT %s""",
        (product_id, product_id, limit),
    ).fetchall()
