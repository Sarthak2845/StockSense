# StockSense

An event-sourced inventory reconciliation engine that keeps one trustworthy stock count across Amazon, Flipkart, a seller's own website and a shop counter. Read the full PRD and plan in [StockSense — PRD & Implementation Plan.md](<StockSense — PRD & Implementation Plan.md>).

## Status

- **Phase 0 (setup), done:** Postgres + pgvector, Redis and RabbitMQ via Docker Compose; schema migration; seed data; the shared [internal event contract](docs/event-contract.md).
- **Phase 1, in progress:** code and tests for [intake/](intake/) (Node), [processor/](processor/) (Python worker with row locking and conflict scoring) and [api/](api/) (FastAPI, read-only), plus [simulators/send.py](simulators/send.py). These services are not in `docker-compose.yml` yet.

## Running the tests

```sh
cd intake && npm ci && npm test
cd processor && pip install -r requirements.txt && pytest
cd api && pip install -r requirements.txt && pytest
```

The database tests in `processor/` and `api/` are skipped unless `TEST_DATABASE_URL` points at a Postgres where the user can create databases. With the compose stack running, that is `postgresql://stocksense:<PG_PASS>@localhost:<PG_PORT>/postgres`.

## Quick start

Requires Docker Desktop (or Docker Engine with Compose v2).

```sh
cp .env.example .env          # then change the passwords
docker compose up -d
docker compose ps             # wait until all three are "healthy"
```

Check the seeded database (Phase 0 gate):

```sh
docker compose exec postgres psql -U stocksense -d stocksense -c "\dt"
docker compose exec -T postgres psql -U stocksense -d stocksense -f /db/checks.sql
```

In Git Bash on Windows, put `MSYS_NO_PATHCONV=1` in front of the second command, or Git Bash rewrites `/db/checks.sql` into a Windows path. If ports 5432 or 6379 are taken by a local Postgres or Redis, set `PG_PORT` or `REDIS_PORT` in `.env`.

`checks.sql` prints the row counts (4 channels, 10 products, 50 customers, 10 opening events). Its snapshot and oversell checks should return no rows.

| Service | Address |
| --- | --- |
| Postgres | `localhost:5432`, db/user from `.env` |
| Redis | `localhost:6379`, password from `.env` |
| RabbitMQ AMQP | `localhost:5672` |
| RabbitMQ management UI | http://localhost:15672 |

All ports bind to `127.0.0.1` only.

### Resetting the database

Migrations and seed data run only when the Postgres volume is empty. After changing `db/`, reset with:

```sh
docker compose down -v && docker compose up -d
```

This deletes all local data, including RabbitMQ queues.

## Layout

See PRD section 13. Each top-level folder is one service; `db/` holds migrations (`db/migrations/NNN_*.sql`, applied in name order) and `db/seed.sql`.

## Key rules

- `events` is append-only. A trigger blocks `UPDATE`, `DELETE` and `TRUNCATE`. Corrections are new `adjust` events.
- `stock_snapshot` is a cache of `SUM(events.quantity)` per product, and a `CHECK` stops it from going below zero.
- Secrets live in `.env` only. The `channels.webhook_secret` column holds a placeholder.
