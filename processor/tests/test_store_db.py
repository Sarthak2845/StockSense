"""Integration tests against a real Postgres.

Skipped unless TEST_DATABASE_URL points at a server where the user may CREATE DATABASE, e.g.
    TEST_DATABASE_URL=postgresql://stocksense:<pass>@localhost:5432/postgres pytest
Each test gets a fresh database built from db/migrations + db/seed.sql, dropped afterwards.
"""

import os
import threading
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import conninfo  # noqa: E402

from conflicts import ScoringConfig  # noqa: E402
from store import UnknownProduct, process_event  # noqa: E402

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")

DB_DIR = Path(__file__).resolve().parents[2] / "db"
CFG = ScoringConfig()


@pytest.fixture
def db_url():
    name = f"stocksense_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = conninfo.make_conninfo(ADMIN_URL, dbname=name)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            for f in sorted((DB_DIR / "migrations").glob("*.sql")):
                conn.execute(f.read_text(encoding="utf-8"))
            conn.execute((DB_DIR / "seed.sql").read_text(encoding="utf-8"))
        yield url
    finally:
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def conn(db_url):
    with psycopg.connect(db_url, autocommit=True) as c:
        yield c


_seq = iter(range(1, 10**9))


def evt(channel="amazon", type_="sale", sku="TIN-6IN", qty=None, price=499.0, order=None, eid=None, cust=None):
    n = next(_seq)
    if qty is None:
        qty = -1 if type_ == "sale" else 1
    return {
        "external_event_id": eid or f"{channel}-{n}",
        "channel": channel,
        "type": type_,
        "sku": sku,
        "quantity": qty,
        "unit_price": price if type_ == "sale" else None,
        "order_id": order or f"{channel.upper()}-ORD-{n}",
        "customer_ref": cust,
        "occurred_at": "2026-11-26T10:00:00.000Z",
    }


def stock(conn, sku):
    return conn.execute(
        "SELECT s.on_hand FROM stock_snapshot s JOIN products p ON p.id = s.product_id WHERE p.sku = %s", (sku,)
    ).fetchone()[0]


def assert_snapshot_matches_events(conn):
    bad = conn.execute(
        """SELECT p.sku FROM products p JOIN stock_snapshot s ON s.product_id = p.id
           LEFT JOIN (SELECT product_id, SUM(quantity) t FROM events GROUP BY product_id) e ON e.product_id = p.id
           WHERE s.on_hand <> COALESCE(e.t, 0)"""
    ).fetchall()
    assert bad == []


def test_sale_reduces_stock_and_is_logged(conn):
    out = process_event(conn, evt(sku="TIN-6IN", qty=-2), CFG)
    assert out.status == "applied"
    assert stock(conn, "TIN-6IN") == 10
    assert_snapshot_matches_events(conn)


def test_same_event_five_times_changes_stock_once(conn):
    e = evt(sku="TIN-6IN")
    statuses = [process_event(conn, e, CFG).status for _ in range(5)]
    assert statuses == ["applied"] + ["duplicate"] * 4
    assert stock(conn, "TIN-6IN") == 11


def test_cancel_and_return_restore_stock_but_never_more_than_was_sold(conn):
    process_event(conn, evt(sku="TIN-LOAF", qty=-3, order="O1"), CFG)
    process_event(conn, evt(type_="cancel", sku="TIN-LOAF", qty=2, order="O1"), CFG)
    out = process_event(conn, evt(type_="return", sku="TIN-LOAF", qty=5, order="O1"), CFG)
    assert out.status == "applied"                    # only 1 unit was still outstanding
    assert stock(conn, "TIN-LOAF") == 15
    assert_snapshot_matches_events(conn)


def test_cancel_arriving_before_its_sale(conn):
    first = process_event(conn, evt(type_="cancel", sku="TIN-LOAF", order="LATE-1"), CFG)
    second = process_event(conn, evt(type_="sale", sku="TIN-LOAF", order="LATE-1"), CFG)
    assert (first.status, second.status) == ("noop", "noop")
    assert stock(conn, "TIN-LOAF") == 15
    assert_snapshot_matches_events(conn)


def test_last_unit_lower_score_incoming_is_cancelled_with_reason(conn):
    process_event(conn, evt(channel="amazon", sku="TIN-8IN", order="AMZ-8812"), CFG)
    out = process_event(conn, evt(channel="website", sku="TIN-8IN", order="WEB-4521"), CFG)
    assert out.status == "conflict"
    assert stock(conn, "TIN-8IN") == 0
    reason, winner_order, loser_order = conn.execute(
        """SELECT c.reason, w.order_id, l.order_id FROM conflicts c
           JOIN events w ON w.id = c.winner_event JOIN events l ON l.id = c.loser_event"""
    ).fetchone()
    assert (winner_order, loser_order) == ("AMZ-8812", "WEB-4521")
    assert "AMZ-8812" in reason and "WEB-4521" in reason
    assert_snapshot_matches_events(conn)


def test_last_unit_higher_score_incoming_takes_it_over(conn):
    process_event(conn, evt(channel="website", sku="TIN-8IN", order="WEB-1"), CFG)
    out = process_event(conn, evt(channel="amazon", sku="TIN-8IN", order="AMZ-1"), CFG)
    assert out.status == "conflict"
    assert stock(conn, "TIN-8IN") == 0
    cancelled = conn.execute(
        "SELECT order_id, caused_by IS NOT NULL FROM events WHERE type = 'cancel'"
    ).fetchall()
    assert cancelled == [("WEB-1", True)]
    assert_snapshot_matches_events(conn)


def test_concurrent_last_unit_race_never_oversells(db_url):
    """20 workers sell the single TIN-8IN at the same instant."""
    channels = ["amazon", "flipkart", "website", "counter"]
    events = [evt(channel=channels[i % 4], sku="TIN-8IN") for i in range(20)]
    barrier = threading.Barrier(len(events))
    errors = []

    def run(e):
        try:
            with psycopg.connect(db_url, autocommit=True) as c:
                barrier.wait()
                process_event(c, e, CFG)
        except Exception as ex:  # noqa: BLE001
            errors.append(ex)

    threads = [threading.Thread(target=run, args=(e,)) for e in events]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    with psycopg.connect(db_url, autocommit=True) as c:
        assert stock(c, "TIN-8IN") == 0
        kept = c.execute(
            """SELECT count(*) FROM events s WHERE s.type = 'sale' AND s.quantity < 0
               AND s.product_id = (SELECT id FROM products WHERE sku = 'TIN-8IN')
               AND NOT EXISTS (SELECT 1 FROM events x WHERE x.type = 'cancel'
                               AND x.channel = s.channel AND x.order_id = s.order_id)"""
        ).fetchone()[0]
        assert kept == 1
        assert c.execute("SELECT count(*) FROM conflicts").fetchone()[0] >= 19
        assert_snapshot_matches_events(c)


def test_unknown_sku_is_rejected(conn):
    with pytest.raises(UnknownProduct):
        process_event(conn, evt(sku="NOPE-1"), CFG)


def test_events_are_append_only(conn):
    process_event(conn, evt(), CFG)
    for sql in ("UPDATE events SET quantity = 0", "DELETE FROM events", "TRUNCATE events CASCADE"):
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(sql)


def test_stock_cannot_go_negative_even_if_code_is_wrong(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE stock_snapshot SET on_hand = -1")
