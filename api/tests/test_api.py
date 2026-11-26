"""API tests.

The first group needs no database. The second runs against a fresh database built
from db/migrations + db/seed.sql when TEST_DATABASE_URL is set (see processor tests).
"""

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import conninfo

import db
from main import app

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
DB_DIR = Path(__file__).resolve().parents[2] / "db"


# --- no database -------------------------------------------------------------

def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_without_database_returns_503():
    assert TestClient(app).get("/products").status_code == 503


def test_bad_time_travel_timestamp_is_422():
    app.dependency_overrides[db.get_conn] = lambda: None
    try:
        assert TestClient(app).get("/products/1/stock?at=not-a-date").status_code == 422
    finally:
        app.dependency_overrides.clear()


# --- with database -----------------------------------------------------------

needs_db = pytest.mark.skipif(not ADMIN_URL, reason="TEST_DATABASE_URL not set")


@pytest.fixture(scope="module")
def client():
    name = f"stocksense_apitest_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = conninfo.make_conninfo(ADMIN_URL, dbname=name)
    try:
        with psycopg.connect(url, autocommit=True) as conn:
            for f in sorted((DB_DIR / "migrations").glob("*.sql")):
                conn.execute(f.read_text(encoding="utf-8"))
            conn.execute((DB_DIR / "seed.sql").read_text(encoding="utf-8"))
            pid = conn.execute("SELECT id FROM products WHERE sku = 'TIN-6IN'").fetchone()[0]
            # One sale an hour after opening stock, so time travel has two points.
            conn.execute(
                """INSERT INTO events (external_event_id, channel, product_id, type, quantity, unit_price,
                                       order_id, occurred_at, recorded_at)
                   VALUES ('amz-1', 'amazon', %s, 'sale', -2, 299, 'AMZ-1', now(), now() + interval '1 hour')""",
                (pid,),
            )
            conn.execute("UPDATE stock_snapshot SET on_hand = on_hand - 2 WHERE product_id = %s", (pid,))
        db.open_pool(url)
        yield TestClient(app), pid
    finally:
        db.close_pool()
        with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@needs_db
def test_products_list_has_stock(client):
    c, _ = client
    rows = c.get("/products").json()
    assert len(rows) == 10
    tin = next(r for r in rows if r["sku"] == "TIN-6IN")
    assert (tin["on_hand"], tin["available"]) == (10, 10)
    assert next(r for r in rows if r["sku"] == "TIN-8IN")["low_stock"] is True


@needs_db
def test_event_timeline_newest_first(client):
    c, pid = client
    body = c.get(f"/products/{pid}/events").json()
    assert [e["type"] for e in body["events"]] == ["sale", "restock"]
    assert c.get("/products/99999/events").status_code == 404


@needs_db
def test_time_travel(client):
    c, pid = client
    now = c.get(f"/products/{pid}/stock").json()
    assert now["stock"] == 10
    from datetime import datetime, timedelta, timezone

    before_sale = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    past = c.get(f"/products/{pid}/stock", params={"at": before_sale}).json()
    assert past["stock"] == 12


@needs_db
def test_conflicts_empty_list(client):
    c, _ = client
    assert c.get("/conflicts").json() == []
