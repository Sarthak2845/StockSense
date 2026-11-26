-- StockSense schema (PRD section 7).
-- The `events` table is the single source of truth; every other table is
-- reference data or a fast view that can be rebuilt from events.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------
CREATE TABLE products (
  id            SERIAL PRIMARY KEY,
  sku           TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  cost_price    NUMERIC(10,2) NOT NULL CHECK (cost_price >= 0),
  low_stock_at  INT DEFAULT 5
);

CREATE TABLE channels (
  code            TEXT PRIMARY KEY,          -- amazon, flipkart, website, counter
  name            TEXT NOT NULL,
  cancel_penalty  NUMERIC(10,2) NOT NULL,    -- cost (Rs) of one seller cancellation
  -- Dev placeholder only. Services read real secrets from .env
  -- (WEBHOOK_SECRET_<CHANNEL>), never from this column.
  webhook_secret  TEXT NOT NULL
);

CREATE TABLE customers (
  id              SERIAL PRIMARY KEY,
  channel         TEXT REFERENCES channels(code),
  external_id     TEXT NOT NULL,
  lifetime_value  NUMERIC(12,2) DEFAULT 0,
  UNIQUE (channel, external_id)
);

-- ---------------------------------------------------------------------------
-- The source of truth: append-only
-- ---------------------------------------------------------------------------
CREATE TABLE events (
  id                 BIGSERIAL PRIMARY KEY,
  external_event_id  TEXT NOT NULL,
  channel            TEXT REFERENCES channels(code),  -- NULL = internal (owner restock, adjust)
  product_id         INT NOT NULL REFERENCES products(id),
  type               TEXT NOT NULL CHECK (type IN
                       ('sale','cancel','return','restock','adjust','reserve','release')),
  quantity           INT NOT NULL,                    -- signed: sale = -1, restock = +10
  unit_price         NUMERIC(10,2),
  order_id           TEXT,
  customer_id        INT REFERENCES customers(id),
  caused_by          BIGINT REFERENCES events(id),    -- e.g. cancel caused by a conflict
  occurred_at        TIMESTAMPTZ NOT NULL,            -- time at the channel
  recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload            JSONB,
  -- Permanent duplicate guard. NULLS NOT DISTINCT so internal events
  -- (channel IS NULL) are de-duplicated too; a plain UNIQUE treats NULLs
  -- as distinct and would let them repeat.
  CONSTRAINT events_channel_external_event_id_key
    UNIQUE NULLS NOT DISTINCT (channel, external_event_id)
);
CREATE INDEX events_product_recorded_idx ON events (product_id, recorded_at);
CREATE INDEX events_order_idx ON events (order_id) WHERE order_id IS NOT NULL;

-- Fast current view (rebuildable from events)
CREATE TABLE stock_snapshot (
  product_id     INT PRIMARY KEY REFERENCES products(id),
  on_hand        INT NOT NULL,
  reserved       INT NOT NULL DEFAULT 0,
  last_event_id  BIGINT REFERENCES events(id),
  updated_at     TIMESTAMPTZ DEFAULT now(),
  -- Last line of defence against oversells: a bug that would push stock
  -- below zero fails the transaction instead of committing it.
  CONSTRAINT stock_non_negative CHECK (on_hand >= 0 AND reserved >= 0)
);

-- ---------------------------------------------------------------------------
-- Decisions and derived records
-- ---------------------------------------------------------------------------
CREATE TABLE conflicts (
  id            BIGSERIAL PRIMARY KEY,
  product_id    INT REFERENCES products(id),
  winner_event  BIGINT REFERENCES events(id),
  loser_event   BIGINT REFERENCES events(id),
  scores        JSONB NOT NULL,      -- every input and score, for explanation
  reason        TEXT NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX conflicts_product_created_idx ON conflicts (product_id, created_at);

CREATE TABLE drifts (
  id            BIGSERIAL PRIMARY KEY,
  product_id    INT REFERENCES products(id),
  channel       TEXT REFERENCES channels(code),
  ours          INT,
  theirs        INT,
  resolved      BOOLEAN DEFAULT false,
  detected_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE allocation_rules (
  id           SERIAL PRIMARY KEY,
  product_id   INT REFERENCES products(id),
  channel      TEXT REFERENCES channels(code),
  kind         TEXT CHECK (kind IN ('reserve','cap')),
  units        INT NOT NULL CHECK (units >= 0),
  active       BOOLEAN DEFAULT true,
  approved_by  TEXT,
  approved_at  TIMESTAMPTZ
);

CREATE TABLE suggestions (
  id            SERIAL PRIMARY KEY,
  rule          JSONB NOT NULL,       -- the candidate allocation rule
  evidence      JSONB NOT NULL,       -- conflict ids, counts
  whatif_result JSONB NOT NULL,       -- cancellations avoided, Rs saved
  status        TEXT DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  created_at    TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- RAG store. 384 dimensions = all-MiniLM-L6-v2; change if the model changes.
-- ---------------------------------------------------------------------------
CREATE TABLE rag_docs (
  id          BIGSERIAL PRIMARY KEY,
  source_type TEXT CHECK (source_type IN ('event','conflict','drift','policy')),
  source_id   TEXT,
  content     TEXT NOT NULL,
  embedding   vector(384)
);
CREATE INDEX rag_docs_embedding_idx ON rag_docs USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- Make events append-only. Corrections are new `adjust` events, never edits.
-- ---------------------------------------------------------------------------
CREATE FUNCTION no_change() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'events are append-only';
END; $$ LANGUAGE plpgsql;

CREATE TRIGGER events_lock BEFORE UPDATE OR DELETE ON events
  FOR EACH ROW EXECUTE FUNCTION no_change();

-- Row triggers do not fire on TRUNCATE, so block it separately.
CREATE TRIGGER events_lock_truncate BEFORE TRUNCATE ON events
  FOR EACH STATEMENT EXECUTE FUNCTION no_change();
