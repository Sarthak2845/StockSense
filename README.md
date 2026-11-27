# StockSense

An event-sourced inventory reconciliation engine that keeps one trustworthy stock count across Amazon, Flipkart, a seller's own website and a shop counter. Read the full PRD and plan in [StockSense — PRD & Implementation Plan.md](<StockSense — PRD & Implementation Plan.md>).

## Status

- **Phase 0 (setup), done:** Postgres + pgvector, Redis and RabbitMQ; schema migration; seed data; the shared [internal event contract](docs/event-contract.md).
- **Phase 1 (event log and intake), done:** [intake/](intake/) (Node), [processor/](processor/) (Python worker with row locking and conflict scoring) and [api/](api/) (FastAPI, read-only) run in Docker Compose. [simulators/send.py](simulators/send.py) sends signed sales, and [simulators/phase1_gate.py](simulators/phase1_gate.py) checks the gate.

Remaining work is tracked as GitHub issues, grouped by phase milestones.

## Quick start

Requires Docker Desktop (or Docker Engine with Compose v2) and Python 3.11+ for the simulator. The simulator uses only the standard library.

```sh
cp .env.example .env          # then change the passwords (URL-safe characters only)
docker compose up -d --build
docker compose ps             # all services "Up", infrastructure and api "healthy"
```

Run the Phase 1 gate (100 signed sales through all 4 channels, then check every number):

```sh
python simulators/phase1_gate.py
python simulators/phase1_gate.py --count 500 --duplicates 0.2   # with resent webhooks
```

It prints a stock table per SKU and a PASS/FAIL line per check. A sale beyond available stock is expected to become a logged conflict, not negative stock. The script exits non-zero if any check fails.

Send traffic without checking:

```sh
python simulators/send.py --count 100 --rate 50
python simulators/send.py --race TIN-8IN       # 4 channels sell the same SKU at once
```

Look at the results:

| What | Where |
| --- | --- |
| API docs (try the endpoints) | http://127.0.0.1:8000/docs |
| Products with stock | http://127.0.0.1:8000/products |
| Conflicts with scores and reasons | http://127.0.0.1:8000/conflicts |
| RabbitMQ management UI | http://127.0.0.1:15672 |
| Worker log | `docker compose logs -f processor` |

## Services

| Service | Host address | Notes |
| --- | --- | --- |
| intake | `127.0.0.1:8080` | `POST /webhooks/{amazon,flipkart,website}`, `POST /pos/sale`, `GET /health` |
| api | `127.0.0.1:8000` | `GET /products`, `/products/{id}/events`, `/products/{id}/stock?at=`, `/conflicts` |
| processor | none | consumes the `events` queue |
| postgres | `127.0.0.1:5432` | db/user from `.env` |
| redis | `127.0.0.1:6379` | password from `.env` |
| rabbitmq | `127.0.0.1:5672`, UI `:15672` | user from `.env` |

All ports bind to `127.0.0.1` only. Change them in `.env` (`INTAKE_PORT`, `API_PORT`, `PG_PORT`, `REDIS_PORT`, `RABBITMQ_PORT`) if a local Postgres or Redis already uses one.

### Windows notes

- Use `127.0.0.1`, not `localhost`, in scripts. On Windows `localhost` tries IPv6 first and adds about 2 seconds to every request.
- In Git Bash, prefix commands that pass container paths with `MSYS_NO_PATHCONV=1`, for example:
  `MSYS_NO_PATHCONV=1 docker compose exec -T postgres psql -U stocksense -d stocksense -f /db/checks.sql`

## Running the tests

```sh
cd intake && npm ci && npm test
cd processor && pip install -r requirements.txt && pytest
cd api && pip install -r requirements.txt && pytest
```

The database tests in `processor/` and `api/` are skipped unless `TEST_DATABASE_URL` points at a Postgres where the user can create databases. With the compose stack running, that is `postgresql://stocksense:<PG_PASS>@127.0.0.1:<PG_PORT>/postgres`. Each test builds and drops its own database.

## Resetting the database

Migrations and seed data run only when the Postgres volume is empty. After changing `db/`, or to get the seed stock back, reset with:

```sh
docker compose down -v && docker compose up -d
```

This deletes all local data, including RabbitMQ queues.

## Layout

See PRD section 13. Each top-level folder is one service. `db/` holds migrations (`db/migrations/NNN_*.sql`, applied in name order), `db/seed.sql` and `db/checks.sql`.

## Key rules

- `events` is append-only. A trigger blocks `UPDATE`, `DELETE` and `TRUNCATE`. Corrections are new `adjust` events.
- `stock_snapshot` is a cache of `SUM(events.quantity)` per product, and a `CHECK` stops it from going below zero.
- The worker acks a message only after the database commit. A crash means redelivery, and the unique constraint makes the retry a no-op.
- Secrets live in `.env` only. The `channels.webhook_secret` column holds a placeholder.
