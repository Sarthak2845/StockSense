# StockSense — PRD & Implementation Plan

Oct 18, 2026 · @Sarthak Rana

## 1. Overview

**StockSense** is a central service that keeps one trustworthy stock count across Amazon, Flipkart, a seller's own website and a physical shop counter, so the seller never sells a unit they do not have.

**Full title:** StockSense: A Self-Explaining, Event-Sourced Inventory Reconciliation Engine for Multi-Channel Sellers.

### The problem

Each sales channel keeps its own idea of stock. When two channels sell the last unit seconds apart, the seller oversells and must cancel, refund and apologise. Five causes make this worse:

- **Slow sync:** cheap tools update every 15 to 30 minutes, so sales in between are invisible.
- **Race conditions:** two sales read the same stock at the same instant and both succeed.
- **Duplicate webhooks:** a channel resends the same sale, and it gets deducted twice.
- **No conflict logic:** systems either reject silently or let stock go negative.
- **No history:** when stock is wrong, nobody can say why.

### The solution

Every change (sale, return, restock, cancel) is stored as a permanent **event**. Current stock is the sum of all events. On top of this log, StockSense adds:

1. Duplicate detection, a durable queue and row locking, so every real sale counts exactly once.
2. Priority-based conflict resolution that weighs profit, customer value and marketplace cancellation penalties.
3. A nightly reconciliation check against each channel.
4. A seller dashboard with stock, profit and full history, including time travel.
5. Human-approved suggestions, backed by a **what-if simulator** that replays past events under new rules.
6. A **RAG assistant** that explains any stock change or conflict in plain language, citing the events it used.

### Scope note

Real Amazon and Flipkart seller APIs need approved seller accounts. This project uses **channel simulators** that send webhooks in the same shape as real marketplaces. This is stated openly in the report and the demo.

## 2. Goals, non-goals and success metrics

The project succeeds if a chaos test of 1,000 events per second, with duplicates and crashes, ends with **zero oversells and zero lost events**.

### Goals

- One correct stock count per product across 4 channels.
- Every real sale counted exactly once, even with duplicates and server crashes.
- Last-unit conflicts resolved by clear, logged rules, never silently.
- Every stock number explainable from history.
- Suggestions that are proven with the seller's own data and approved by a human.

### Non-goals

- Live integration with real Amazon or Flipkart accounts.
- Payments, shipping labels or order fulfilment.
- Machine-learning models. Analysis is rule-based and SQL-based.
- Automatic money decisions. The system never changes allocation rules without approval.
- Multi-tenant SaaS. One seller per deployment.

### Success metrics

| Metric | Target | How measured |
| --- | --- | --- |
| Oversells under chaos test | 0 | Final stock never below 0; conflict log matches |
| Lost events after worker crash | 0 | Events sent = events in log |
| Duplicate deductions | 0 | Replayed webhooks change nothing |
| Webhook response time (p95) | under 100 ms | k6 load test |
| Sustained throughput | 1,000 events/s | k6 load test, 3 intake nodes |
| Event to dashboard delay | under 2 s | Timestamp diff |
| Reconciliation drift detected | 100% of injected drifts | Simulator injects wrong stock |
| RAG answers with correct source | 9 of 10 test questions | Manual evaluation set |

## 3. Users and user stories

The main user is a small or mid-size seller who lists the same products on 3 or more channels and cannot afford enterprise tools.

| Persona | Who | Needs |
| --- | --- | --- |
| Seller (Owner) | Runs the business, e.g. a home baker selling tins and kits on 4 channels | Correct stock, fewer cancellations, clear reasons, profit view |
| Counter staff | Records walk-in sales at the shop | Fast sale entry, works with patchy internet |
| Admin / developer | Your team during the demo | Health metrics, logs, chaos controls |

### User stories

- As a seller, I want stock to update on all channels within seconds, so I do not oversell.
- As a seller, when two orders fight for the last unit, I want the system to keep the more valuable one and tell me why.
- As a seller, I want to see what my stock was at any past time, so I can find mistakes.
- As a seller, I want suggestions like "reserve 2 units for Flipkart" with proof of how much they would have saved.
- As a seller, I want to approve or reject every suggestion before it takes effect.
- As a seller, I want to ask "Why was order #4521 cancelled?" and get a plain answer with sources.
- As a seller, I want a nightly report if any channel's stock differs from mine.
- As counter staff, I want to record a sale in one click, even if the internet drops.
- As an admin, I want to kill a server mid-demo and prove that no event is lost.

## 4. Functional requirements

Requirements are numbered by module. **P0** = must have for the demo, **P1** = should have, **P2** = only if time allows.

### M1. Channel simulators

- **FR-1.1 (P0)** Simulate 4 channels: `amazon`, `flipkart`, `website`, `counter`. Each sends sale, cancel and return webhooks in its own JSON shape.
- **FR-1.2 (P0)** Each webhook carries a unique `event_id` from the channel, an HMAC signature header and a timestamp.
- **FR-1.3 (P0)** Chaos modes: resend the same event (duplicates), fire two sales for the last unit within 5 ms, delay events, send out of order.
- **FR-1.4 (P1)** Each simulator keeps its own local stock and exposes `GET /stock` for reconciliation. Drift can be injected on purpose.

### M2. Webhook intake (Node.js + Express, 3 nodes behind Nginx)

- **FR-2.1 (P0)** `POST /webhooks/:channel` verifies the HMAC signature. Invalid signature returns 401.
- **FR-2.2 (P0)** Normalise each channel's payload into one internal event format.
- **FR-2.3 (P0)** Check Redis for the `channel:event_id` key. If present, return 200 and stop.
- **FR-2.4 (P0)** Publish the event to RabbitMQ with publisher confirms, then return 202. Never return success before the broker confirms.
- **FR-2.5 (P0)** `POST /pos/sale` for the shop counter, authenticated with a staff token.

### M3. Event processor (Python worker)

- **FR-3.1 (P0)** Consume from the queue with manual acknowledgement. Ack only after the database transaction commits.
- **FR-3.2 (P0)** Insert into `events` with a unique constraint on `(channel, external_event_id)`. A duplicate insert is a no-op. This is the second, permanent duplicate guard.
- **FR-3.3 (P0)** Lock the product's stock row with `SELECT ... FOR UPDATE`, apply the change, and update the `stock_snapshot` table in the same transaction.
- **FR-3.4 (P0)** If a sale would make available stock negative, create a conflict instead of applying it blindly.
- **FR-3.5 (P0)** After commit, push the new stock level to every channel simulator and to the dashboard over WebSocket.
- **FR-3.6 (P1)** Failed messages retry 3 times, then move to a dead-letter queue shown on the admin page.

### M4. Conflict resolution

- **FR-4.1 (P0)** Collect all competing orders for the product within a 2-second conflict window.
- **FR-4.2 (P0)** Score each order using margin, customer value and channel cancellation penalty (formula in section 9).
- **FR-4.3 (P0)** Keep the highest scores up to available stock. Write a `cancel` event for each loser, with a reason.
- **FR-4.4 (P0)** Store each decision in `conflicts` with all scores, so it can be explained later.
- **FR-4.5 (P1)** Soft cancel: offer the losing customer a substitute product or restock date, drafted for seller approval.

### M5. Reconciliation

- **FR-5.1 (P1)** A scheduled job (nightly, and on demand) asks each channel for its stock and compares it with StockSense.
- **FR-5.2 (P1)** Any difference is saved as a `drift` record and shown on the dashboard. The seller can push the correct value with one click.

### M6. Seller dashboard

- **FR-6.1 (P0)** Live stock per product and per channel, updated over WebSocket.
- **FR-6.2 (P0)** Conflict list: each row shows winner, loser, scores and reason.
- **FR-6.3 (P0)** Event timeline for any product.
- **FR-6.4 (P1)** Time travel: a slider shows stock at any past moment.
- **FR-6.5 (P1)** Profit per order and per channel, top customers.
- **FR-6.6 (P1)** Low-stock alerts based on 7-day sales speed.
- **FR-6.7 (P1)** Login with roles: owner and staff (JWT).

### M7. Suggestions and what-if simulator (the innovation)

- **FR-7.1 (P0)** A daily job groups conflicts by channel and product and finds patterns over a threshold (for example, 5 or more lost-margin cancellations in 30 days).
- **FR-7.2 (P0)** For each pattern, generate a candidate rule, such as "reserve 2 units of product X for Flipkart".
- **FR-7.3 (P0)** The what-if simulator replays the last 30 days of events with and without the rule, and reports cancellations avoided and money saved.
- **FR-7.4 (P0)** Show the suggestion with its evidence. Approve creates an active `allocation_rule`. Reject records the decision.
- **FR-7.5 (P1)** The seller can also run a what-if with any custom rule.

### M8. RAG explainability assistant

- **FR-8.1 (P1)** Every event, conflict and drift is turned into a short English sentence and stored with a vector embedding (pgvector).
- **FR-8.2 (P1)** Marketplace policy notes (written by the team from public seller help pages) are chunked and embedded too.
- **FR-8.3 (P1)** The seller asks a question in the dashboard. The system retrieves the top matching sentences, runs SQL for any numbers, and the LLM answers only from that context.
- **FR-8.4 (P1)** Every answer lists its sources (event IDs, conflict IDs, policy chunk). If nothing matches, it says it does not know.
- **FR-8.5 (P2)** Answer in Hindi or Marathi on request.

### M9. Observability and chaos

- **FR-9.1 (P1)** Prometheus metrics: events per second, queue depth, conflicts, processing latency. Grafana dashboard.
- **FR-9.2 (P1)** A chaos script that stops and restarts containers during a load test.

## 5. Non-functional requirements

| Area | Requirement |
| --- | --- |
| Correctness | Exactly-once effect per real sale: at-least-once delivery plus idempotent processing |
| Durability | No accepted event is lost if any one container crashes (durable queue, persistent messages, manual ack) |
| Performance | p95 webhook response under 100 ms; 1,000 events/s sustained on a laptop with 16 GB RAM |
| Freshness | Stock change visible on dashboard and simulators in under 2 s |
| Auditability | Events are append-only. No UPDATE or DELETE on `events`, enforced by a database trigger |
| Security | HMAC on webhooks, JWT for dashboard, secrets in `.env`, nothing sensitive in URLs |
| Explainability | Every conflict and RAG answer links to the event IDs behind it |
| Portability | Whole system starts with `docker compose up` |
| Safety of AI | LLM is read-only. It never writes events or rules |

## 6. System architecture

Webhooks enter through Nginx and 3 Node.js intake nodes, pass a Redis duplicate check, wait safely in RabbitMQ, and are written by Python workers into one append-only event log in PostgreSQL.

&#91;embedded content: StockSense architecture · 10 components, 1 event log\]

The event log (highlighted) is the only source of truth; the dashboard, what-if simulator and RAG assistant all read from it.

### Life of one sale

1. Flipkart's simulator sends `POST /webhooks/flipkart` with a signed payload.
2. Nginx forwards it to one of the 3 intake nodes.
3. Intake verifies the signature, normalises the payload, and checks Redis. A repeat stops here with 200.
4. Intake publishes to RabbitMQ, waits for the broker's confirm, then replies 202 within about 20 ms.
5. A worker takes the message, inserts the event (unique constraint as the second guard), and locks the product's stock row.
6. If stock is available, it applies the sale. If not, it scores the competing orders and writes a `cancel` event for the loser.
7. The transaction commits; only then does the worker ack the message.
8. The worker pushes the new available stock to all 4 channels and to the dashboard over WebSocket.
9. A sentence describing any conflict is embedded into pgvector for the RAG assistant.

### Components

| Component | Technology | Why this choice |
| --- | --- | --- |
| Intake | Node.js + Express | Fast, non-blocking I/O for many small webhook requests |
| Load balancer | Nginx | Spreads traffic and lets one node fail without downtime |
| Duplicate cache | Redis | Atomic `SET NX` check in under 1 ms |
| Queue | RabbitMQ | Durable messages, publisher confirms, manual acks, dead-letter queue |
| Worker | Python | Clear code for conflict logic and data work |
| Database | PostgreSQL + pgvector | Transactions and row locks; vectors for RAG in the same database |
| API | FastAPI | Quick to build, automatic API docs for the report |
| Dashboard | HTML/JS or React | Live grid, timeline, suggestion cards |
| Embeddings | sentence-transformers | Free, runs on CPU |
| Packaging | Docker Compose | One command starts everything |

## 7. Data model

The `events` table is the single source of truth; every other table is either reference data or a fast view rebuilt from events.

```sql
-- Reference data
CREATE TABLE products (
  id            SERIAL PRIMARY KEY,
  sku           TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  cost_price    NUMERIC(10,2) NOT NULL,
  low_stock_at  INT DEFAULT 5
);

CREATE TABLE channels (
  code                 TEXT PRIMARY KEY,      -- amazon, flipkart, website, counter
  name                 TEXT NOT NULL,
  cancel_penalty       NUMERIC(10,2) NOT NULL, -- cost (Rs) of one seller cancellation
  webhook_secret       TEXT NOT NULL
);

CREATE TABLE customers (
  id            SERIAL PRIMARY KEY,
  channel       TEXT REFERENCES channels(code),
  external_id   TEXT NOT NULL,
  lifetime_value NUMERIC(12,2) DEFAULT 0,
  UNIQUE (channel, external_id)
);

-- The source of truth: append-only
CREATE TABLE events (
  id                 BIGSERIAL PRIMARY KEY,
  external_event_id  TEXT NOT NULL,
  channel            TEXT REFERENCES channels(code),
  product_id         INT REFERENCES products(id),
  type               TEXT NOT NULL CHECK (type IN
                       ('sale','cancel','return','restock','adjust','reserve','release')),
  quantity           INT NOT NULL,           -- signed: sale = -1, restock = +10
  unit_price         NUMERIC(10,2),
  order_id           TEXT,
  customer_id        INT REFERENCES customers(id),
  caused_by          BIGINT REFERENCES events(id), -- e.g. cancel caused by a conflict
  occurred_at        TIMESTAMPTZ NOT NULL,   -- time at the channel
  recorded_at        TIMESTAMPTZ DEFAULT now(),
  payload            JSONB,
  UNIQUE (channel, external_event_id)        -- permanent duplicate guard
);
CREATE INDEX ON events (product_id, recorded_at);

-- Fast current view (rebuildable from events)
CREATE TABLE stock_snapshot (
  product_id     INT PRIMARY KEY REFERENCES products(id),
  on_hand        INT NOT NULL,
  reserved       INT NOT NULL DEFAULT 0,
  last_event_id  BIGINT,
  updated_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE conflicts (
  id            BIGSERIAL PRIMARY KEY,
  product_id    INT REFERENCES products(id),
  winner_event  BIGINT REFERENCES events(id),
  loser_event   BIGINT REFERENCES events(id),
  scores        JSONB NOT NULL,      -- every input and score, for explanation
  reason        TEXT NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE drifts (
  id            BIGSERIAL PRIMARY KEY,
  product_id    INT REFERENCES products(id),
  channel       TEXT REFERENCES channels(code),
  ours          INT, theirs INT,
  resolved      BOOLEAN DEFAULT false,
  detected_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE allocation_rules (
  id           SERIAL PRIMARY KEY,
  product_id   INT REFERENCES products(id),
  channel      TEXT REFERENCES channels(code),
  kind         TEXT CHECK (kind IN ('reserve','cap')),
  units        INT NOT NULL,
  active       BOOLEAN DEFAULT true,
  approved_by  TEXT, approved_at TIMESTAMPTZ
);

CREATE TABLE suggestions (
  id            SERIAL PRIMARY KEY,
  rule          JSONB NOT NULL,       -- the candidate allocation rule
  evidence      JSONB NOT NULL,       -- conflict ids, counts
  whatif_result JSONB NOT NULL,       -- cancellations avoided, Rs saved
  status        TEXT DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- RAG store (needs: CREATE EXTENSION vector;)
CREATE TABLE rag_docs (
  id          BIGSERIAL PRIMARY KEY,
  source_type TEXT,     -- event, conflict, drift, policy
  source_id   TEXT,
  content     TEXT NOT NULL,
  embedding   vector(384)
);

-- Make events append-only
CREATE FUNCTION no_change() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'events are append-only'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER events_lock BEFORE UPDATE OR DELETE ON events
  FOR EACH ROW EXECUTE FUNCTION no_change();
```

**Key ideas to explain in the viva:**

- Stock at any time = `SUM(quantity)` of events up to that time. Time travel is one query.
- `stock_snapshot` exists only for speed and locking. If it is ever wrong, rebuild it from `events`.
- Corrections are new `adjust` events, never edits. That is why history can always be trusted.
- The embedding size 384 matches the free `all-MiniLM-L6-v2` model; change it if you use a different one.

## 8. API specification

All APIs are JSON over HTTP; the dashboard also opens one WebSocket for live updates.

### Intake (Node.js, behind Nginx)

| Method | Path | Auth | Purpose | Responses |
| --- | --- | --- | --- | --- |
| POST | `/webhooks/:channel` | HMAC header `X-Signature` | Receive sale, cancel, return | 202 queued, 200 duplicate, 401 bad signature, 400 bad payload |
| POST | `/pos/sale` | Staff JWT | Counter sale | 202 queued |
| GET | `/health` | none | Liveness for Nginx and Docker | 200 |

Internal event published to RabbitMQ:

```json
{
  "external_event_id": "amz-7f3a91",
  "channel": "amazon",
  "type": "sale",
  "sku": "TIN-8IN",
  "quantity": -1,
  "unit_price": 499.00,
  "order_id": "AMZ-ORD-8812",
  "customer_ref": "amz-cust-221",
  "occurred_at": "2026-10-18T14:02:11.204Z"
}
```

### Dashboard API (Python, FastAPI)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/auth/login` | Returns JWT with role |
| GET | `/products` | Products with current stock |
| GET | `/products/:id/events?from=&to=` | Event timeline |
| GET | `/products/:id/stock?at=2026-10-17T15:15:00Z` | Time travel |
| POST | `/products/:id/restock` | Owner adds stock (writes a `restock` event) |
| GET | `/conflicts?limit=50` | Conflict list with scores and reasons |
| GET | `/drifts` | Reconciliation differences |
| POST | `/drifts/:id/fix` | Push our value to the channel |
| GET | `/suggestions?status=pending` | Suggestions with evidence and what-if result |
| POST | `/suggestions/:id/approve` | Creates an allocation rule |
| POST | `/suggestions/:id/reject` | Records rejection |
| POST | `/whatif` | Body: a rule and a date range. Returns before vs after |
| POST | `/ask` | Body: question. Returns answer and sources |
| GET | `/analytics/profit?group=channel` | Profit per channel or order |
| WS | `/ws` | Pushes `stock_changed`, `conflict`, `drift`, `suggestion` messages |

### Channel simulators

| Method | Path | Purpose |
| --- | --- | --- |
| PUT | `/stock/:sku` | StockSense pushes the new available quantity |
| GET | `/stock` | Reconciliation reads the channel's view |
| POST | `/chaos` | Start a scenario: `duplicates`, `last_unit_race`, `delay`, `drift` |

## 9. Core algorithms

Six algorithms carry the project; each one below has the logic and starter code.

### 9.1 Duplicate check (two layers)

Layer 1 is fast and in Redis. Layer 2 is permanent and in Postgres. Redis alone is not enough, because keys expire and Redis can restart.

```javascript
// intake/src/routes/webhook.js
const key = `evt:${channel}:${evt.external_event_id}`;
const isNew = await redis.set(key, '1', { NX: true, EX: 86400 }); // 24 h
if (!isNew) return res.status(200).json({ status: 'duplicate' });

try {
  await publishWithConfirm('events', evt);   // waits for broker ack
  return res.status(202).json({ status: 'queued' });
} catch (e) {
  await redis.del(key);                       // let the channel retry
  return res.status(503).json({ status: 'retry' });
}
```

In the worker, `INSERT ... ON CONFLICT (channel, external_event_id) DO NOTHING RETURNING id` returns no row for a duplicate, so the worker just acks and stops.

### 9.2 Processing with row locking (no race conditions)

```python
# processor/worker.py
def handle(msg):
    evt = json.loads(msg.body)
    with db.transaction() as cur:
        cur.execute("""INSERT INTO events (...) VALUES (...)
                       ON CONFLICT (channel, external_event_id) DO NOTHING
                       RETURNING id""", evt)
        row = cur.fetchone()
        if row is None:
            return ack(msg)                    # duplicate, already applied
        event_id = row[0]

        cur.execute("""SELECT on_hand, reserved FROM stock_snapshot
                       WHERE product_id = %s FOR UPDATE""", (evt['product_id'],))
        on_hand, reserved = cur.fetchone()     # other workers wait here

        available = on_hand - reserved_for_others(evt['channel'], evt['product_id'])
        if evt['type'] == 'sale' and available + evt['quantity'] < 0:
            open_conflict(cur, event_id, evt)  # see 9.3
        else:
            cur.execute("""UPDATE stock_snapshot
                           SET on_hand = on_hand + %s, last_event_id = %s, updated_at = now()
                           WHERE product_id = %s""",
                        (evt['quantity'], event_id, evt['product_id']))
    ack(msg)                                   # only after COMMIT
    broadcast_stock(evt['product_id'])
```

Why it works: `FOR UPDATE` makes the second sale wait until the first transaction commits, so it reads the new stock, not the old one.

### 9.3 Conflict scoring

When the last unit is contested, every competing sale inside the 2-second window gets a score. The highest scores keep the units.

```latex
\text{score} = w_1 \cdot \text{margin} + w_2 \cdot \text{customerValue} + w_3 \cdot \text{cancelPenalty}_{\text{channel}} - w_4 \cdot \text{lateness}
```

- **margin** = unit price minus cost price (Rs).
- **customerValue** = customer's lifetime value, scaled 0 to 100.
- **cancelPenalty** = how much a seller cancellation on this channel hurts (higher on marketplaces, near 0 on your own website). Cancelling the lower-penalty order is safer.
- **lateness** = milliseconds after the first order, a small tie-breaker so earlier orders win ties.
- Start with `w1 = 1, w2 = 0.5, w3 = 1, w4 = 0.01` and keep them in a config file.

The loser gets a `cancel` event with `caused_by` pointing to the winning sale. The `conflicts.scores` JSON stores every input, so the dashboard and the RAG assistant can explain the decision.

Note: with a 2-second window, the first sale is held as "pending" until the window closes. For the demo, a simpler version is fine: the first committed sale wins, and the score is used only to choose which order to cancel when a channel reports a sale that was already oversold.

### 9.4 Time travel

```sql
SELECT COALESCE(SUM(quantity), 0) AS stock
FROM events
WHERE product_id = $1 AND recorded_at <= $2;
```

### 9.5 What-if simulator

Replays history through a pure Python function, so it never touches real data.

```python
def replay(events, rules):
    stock, reserved = defaultdict(int), build_reservations(rules)
    cancels, lost_margin = 0, 0
    for e in sorted(events, key=lambda e: e.occurred_at):
        if e.type in ('restock', 'return', 'adjust'):
            stock[e.product_id] += e.quantity
        elif e.type == 'sale':
            free = stock[e.product_id] - reserved.others(e.channel, e.product_id)
            if free >= 1 or reserved.mine(e.channel, e.product_id) >= 1:
                stock[e.product_id] -= 1
                reserved.consume(e.channel, e.product_id)
            else:
                cancels += 1
                lost_margin += e.margin
    return {'cancellations': cancels, 'lost_margin': lost_margin}

before = replay(last_30_days, current_rules)
after  = replay(last_30_days, current_rules + [candidate])
saving = before['lost_margin'] - after['lost_margin']
```

The suggestion is shown only if `after` is better than `before`. Being honest: this assumes customers would have bought the same things; say so in the report as a limitation.

### 9.6 Suggestion generation

1. Daily SQL groups the last 30 days of conflicts by `(product, losing channel)`.
2. Any group with 5 or more conflicts becomes a candidate: reserve N units for that channel, where N = the largest number of that channel's sales lost in a single day.
3. Run the what-if for each candidate. Keep only those that save money.
4. Save to `suggestions` with evidence and result. The seller approves or rejects.

### 9.7 RAG pipeline

1. **Write:** after each conflict, drift or notable event, build a sentence, e.g. "Oct 3, 2:14 PM: website order WEB-4521 for TIN-8IN cancelled; Amazon order AMZ-8812 kept (score 412 vs 188, higher margin and higher cancel penalty)."
2. **Embed:** `sentence-transformers` model `all-MiniLM-L6-v2` (free, runs on CPU) and store in `rag_docs`.
3. **Retrieve:** embed the question, then `ORDER BY embedding <=> $q LIMIT 8`. Also filter by order ID or SKU if the question mentions one.
4. **Numbers:** for questions like "how many" or "how much", run a fixed, pre-written SQL query (chosen by simple keyword matching) and pass its result as context. The LLM never calculates.
5. **Answer:** prompt the LLM with a strict instruction to answer only from context, cite IDs, and say "I don't know" otherwise. Any API (Claude, Gemini) or a local model through Ollama works.
6. **Show sources:** the dashboard renders clickable event and conflict IDs under the answer.

## 10. Implementation plan

The build takes 14 weeks in 6 phases, and the P0 core (phases 0 to 3) is finished by week 7, leaving time for the innovation, testing and report.

&#91;embedded content: build roadmap · 7 phases over 14 weeks\]

Week 1 is whenever your team starts; the dashed line marks the week-7 gate where duplicates, locking and conflicts must already work.

Each phase ends with a gate: a demo the whole team runs before moving on.

### Phase 0: Setup (week 1)

- [ ] Create a GitHub repo with the folder structure from section 13; protect `main`, use pull requests.
- [ ] Write `docker-compose.yml` with Postgres, Redis and RabbitMQ only.
- [ ] Run the schema from section 7 as a migration; add seed data (10 products, 4 channels, 50 customers).
- [ ] Agree the internal event JSON (section 8) as a shared contract.

* **Gate:** `docker compose up` starts the stack; `psql` shows seeded tables.

### Phase 1: Event log and intake (weeks 2 to 3)

- [ ] Node intake: `POST /webhooks/:channel`, payload normaliser per channel, HMAC check.
- [ ] Python worker v1: consume, insert event, update `stock_snapshot` (no locking yet).
- [ ] Simulator v1: one script that sends sales for 4 channels at a chosen rate.
- [ ] API: `GET /products`, `GET /products/:id/events`.

* **Gate:** 100 simulated sales produce 100 events and correct stock.

### Phase 2: Duplicates, queue, crash safety (weeks 4 to 5)

- [ ] Redis `SET NX` check in intake; unique constraint in Postgres.
- [ ] RabbitMQ durable queue, persistent messages, publisher confirms, manual ack in the worker.
- [ ] Retry with dead-letter queue.
- [ ] Simulator chaos mode: `duplicates`.
- [ ] Test: kill the worker during a run, restart it, compare counts.

* **Gate:** 1,000 events with 20% duplicates and one worker crash give exactly the right stock.

### Phase 3: Race conditions and conflicts (weeks 6 to 7)

- [ ] `SELECT ... FOR UPDATE` in the worker; run 2 worker replicas.
- [ ] Conflict detection, scoring (section 9.3), loser `cancel` event, `conflicts` table.
- [ ] Push new stock to every simulator after commit.
- [ ] Simulator chaos mode: `last_unit_race`.
- [ ] Add Nginx and scale intake to 3 nodes.

* **Gate:** 200 last-unit races, 0 oversells, every conflict has a stored reason. **P0 core complete.**

### Phase 4: Dashboard and reconciliation (weeks 8 to 9)

- [ ] FastAPI dashboard API (section 8) and JWT login with owner and staff roles.
- [ ] Web UI: live stock grid over WebSocket, conflict list, event timeline, time-travel slider.
- [ ] Profit per channel and top customers.
- [ ] Reconciliation job and drift list with one-click fix; simulator chaos mode `drift`.
- [ ] Low-stock alerts.

* **Gate:** seller can watch a live race on the dashboard and see why each order was cancelled.

### Phase 5: Innovation, suggestions, what-if and RAG (weeks 10 to 12)

- [ ] What-if replay engine (section 9.5) with unit tests on small hand-made event lists.
- [ ] Daily suggestion job (section 9.6), suggestion cards with evidence, approve and reject.
- [ ] Approved rules feed into the worker's `reserved_for_others` check.
- [ ] RAG: sentence builder, embeddings in pgvector, `/ask` endpoint, chat box with sources.
- [ ] Write 3 to 5 pages of marketplace policy notes for the RAG store.
- [ ] Build a 10-question evaluation set for the assistant.

* **Gate:** a generated suggestion shows Rs saved; after approval, a re-run of the same scenario has fewer cancellations. The assistant answers 9 of 10 test questions with correct sources.

### Phase 6: Testing, hardening, report (weeks 13 to 14)

- [ ] k6 load test at 1,000 events/s; record p95 latency and throughput.
- [ ] Chaos script: stop and restart intake, worker and RabbitMQ during the load test.
- [ ] Prometheus and Grafana dashboard.
- [ ] Fix bugs only; no new features.
- [ ] Final report, presentation slides, 5-minute recorded demo as a backup.

* **Gate:** full demo rehearsal twice without help.

If the team falls behind, cut in this order: Grafana, Hindi answers, profit analytics, Nginx with 3 nodes. Never cut the duplicate, locking or conflict work.

## 11. Team split (4 members)

Each member owns one layer end to end, but everyone must be able to explain the whole flow in the viva.

| Member | Owns | Main deliverables | Also helps with |
| --- | --- | --- | --- |
| A: Intake lead | Node.js, Nginx, Redis | Webhook routes, HMAC, normalisers, duplicate check, publisher confirms, k6 tests | Channel simulators |
| B: Processing lead | Python worker, RabbitMQ, Postgres | Worker, locking, conflict scoring, reconciliation job, chaos script | Schema and migrations |
| C: Product lead | FastAPI, web dashboard | API, login, live grid, timeline, time travel, profit view, suggestion cards | Report screenshots and slides |
| D: Intelligence lead | What-if, suggestions, RAG | Replay engine, suggestion job, pgvector, `/ask`, evaluation set, policy notes | Report writing and literature survey |

If you are 3 members, merge C and D. If you are 2, drop RAG to P2 and skip Nginx scaling.

Weekly routine: a 30-minute sync each Monday to review the last gate, and a pull request reviewed by one other member before merging.

## 12. Testing and demo plan

The proof that StockSense works is numbers from repeatable tests, shown live in a 10-minute demo.

### Tests

| Type | Tool | What it proves |
| --- | --- | --- |
| Unit | pytest, Jest | Normalisers, scoring formula, replay engine on small hand-made event lists |
| Integration | pytest + Docker test stack | Webhook to event to stock, end to end |
| Duplicate | Simulator `duplicates` mode | Same event sent 5 times changes stock once |
| Race | Simulator `last_unit_race`, 200 runs | 0 oversells; every conflict logged |
| Crash | Chaos script (docker stop / start) | Events sent = events stored |
| Load | k6 | 1,000 events/s, p95 under 100 ms |
| Reconciliation | Simulator `drift` mode | Every injected drift is detected |
| What-if | Known scenario with an obvious answer | Saving matches a hand calculation |
| RAG | 10-question evaluation set | 9 of 10 correct, with correct sources |

Keep every result as a table and a graph in the report (latency curve, events sent vs stored, conflicts per channel).

### Demo script (10 minutes)

1. **The problem (1 min):** show two channels both selling the last cake tin without StockSense.
2. **Normal flow (1 min):** simulators send sales; dashboard stock updates live.
3. **Duplicates (1 min):** resend the same Amazon webhook 5 times; stock changes once.
4. **Race (2 min):** fire a last-unit race; one order wins, the other is cancelled; open the conflict and show the scores.
5. **Crash (1 min):** stop the worker during a load run, restart it, show counts match.
6. **Time travel (1 min):** drag the slider to yesterday 3:15 PM.
7. **Suggestion (2 min):** open a suggestion, show the what-if saving, approve it, re-run the scenario, show fewer cancellations.
8. **Ask (1 min):** type "Why was order WEB-4521 cancelled?" and show the answer with sources.

Record the full demo in advance as a backup in case the laptop or Wi-Fi fails.

## 13. Repository structure and Docker Compose

One repo, one folder per service, and the whole system starts with `docker compose up --build`.

```text
stocksense/
├── docker-compose.yml
├── .env.example
├── nginx/
│   └── nginx.conf
├── intake/                 # Node.js + Express
│   ├── src/routes/webhook.js
│   ├── src/normalisers/{amazon,flipkart,website,counter}.js
│   ├── src/queue.js        # RabbitMQ publisher with confirms
│   └── test/
├── processor/              # Python worker
│   ├── worker.py
│   ├── conflicts.py
│   ├── reconcile.py
│   └── tests/
├── api/                    # FastAPI dashboard API
│   ├── main.py
│   ├── routes/{products,conflicts,suggestions,whatif,ask}.py
│   ├── intelligence/{replay.py,suggest.py,rag.py}
│   └── tests/
├── dashboard/              # HTML/JS (or React)
├── simulators/             # fake channels + chaos modes
│   └── channel.py
├── db/
│   ├── migrations/001_schema.sql
│   └── seed.sql
├── loadtest/k6-webhooks.js
├── chaos/chaos.sh
├── monitoring/{prometheus.yml,grafana/}
└── docs/                   # report, diagrams, policy notes
```

```yaml
# docker-compose.yml (core services)
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment: { POSTGRES_DB: stocksense, POSTGRES_PASSWORD: ${PG_PASS} }
    volumes: [ pgdata:/var/lib/postgresql/data, ./db/migrations:/docker-entrypoint-initdb.d ]
  redis:
    image: redis:7
  rabbitmq:
    image: rabbitmq:3-management
    ports: [ "15672:15672" ]          # management UI for the demo
  intake:
    build: ./intake
    deploy: { replicas: 3 }
    depends_on: [ redis, rabbitmq ]
  nginx:
    image: nginx:stable
    volumes: [ ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro ]
    ports: [ "8080:80" ]
    depends_on: [ intake ]
  processor:
    build: ./processor
    deploy: { replicas: 2 }
    depends_on: [ postgres, rabbitmq ]
  api:
    build: ./api
    ports: [ "8000:8000" ]
    depends_on: [ postgres ]
  simulators:
    build: ./simulators
    depends_on: [ nginx ]
volumes:
  pgdata:
```

RabbitMQ and Redis versions are examples; pin whatever version you start with and keep it for the whole project.

## 14. Risks, viva preparation and report outline

The biggest risk is scope: finish the P0 core before touching the innovation.

### Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Too many technologies to learn | Late core, weak viva answers | Phase 0 spike: each member builds a 1-day hello-world of their tool |
| No real marketplace access | Examiners question realism | Say it upfront; model simulator payloads on public seller API docs |
| Locking slows throughput | Misses 1,000 events/s target | Lock per product row only; measure; lower target honestly if needed |
| What-if assumes same customer behaviour | Savings look inflated | State as a limitation; show it as an estimate, not a promise |
| LLM gives a wrong answer | Loss of trust | Answer only from retrieved context, always show sources, LLM is read-only |
| Laptop or Wi-Fi fails in the demo | Demo collapses | Recorded backup video; local LLM through Ollama as a fallback |
| Scope creep | Nothing finished | Gates per phase; cut list in section 10 |

### Viva questions to prepare

- What is a race condition, and how does `SELECT ... FOR UPDATE` prevent it here?
- Why two duplicate checks (Redis and Postgres)? What if Redis restarts?
- What happens if the worker crashes after inserting the event but before acking? (Answer: the message is redelivered; the unique constraint makes the second insert a no-op.)
- Why event sourcing instead of a single stock column?
- How do you prove 0 oversells? Show the numbers.
- How is the what-if simulator different from the static rules in enterprise tools?
- What stops the RAG assistant from making things up?
- What would change to support real Amazon and Flipkart?

### Report outline

1. Introduction and problem statement
2. Literature survey (event sourcing, idempotency, inventory allocation, RAG; search Google Scholar and IEEE)
3. Gap analysis (existing tools vs StockSense)
4. Requirements (sections 2 to 5 of this doc)
5. System design: architecture, data model, APIs
6. Algorithms: duplicates, locking, conflict scoring, what-if, RAG
7. Implementation
8. Testing and results, with graphs
9. Limitations and future work (real APIs, multi-tenant, forecasting)
10. Conclusion and references
