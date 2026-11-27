-- StockSense seed data: 4 channels, 10 products, 50 customers, opening stock.
-- Deterministic (no random()) so tests and demos see the same numbers every run.

BEGIN;

-- Channels. cancel_penalty = cost (Rs) of one seller-side cancellation:
-- high on marketplaces (ratings, account health), near zero on our own site.
-- webhook_secret values are dev placeholders; services use .env secrets.
INSERT INTO channels (code, name, cancel_penalty, webhook_secret) VALUES
  ('amazon',   'Amazon',            250.00, 'dev-placeholder-see-env'),
  ('flipkart', 'Flipkart',          200.00, 'dev-placeholder-see-env'),
  ('website',  'Own website',        20.00, 'dev-placeholder-see-env'),
  ('counter',  'Shop counter (POS)',  0.00, 'dev-placeholder-see-env');

-- Products: a home baker selling tins and kits.
INSERT INTO products (sku, name, cost_price, low_stock_at) VALUES
  ('TIN-6IN',     '6-inch round cake tin',           180.00, 5),
  ('TIN-8IN',     '8-inch round cake tin',           240.00, 5),
  ('TIN-LOAF',    'Non-stick loaf tin',              210.00, 5),
  ('TIN-MUFFIN',  '12-cup muffin tray',              320.00, 4),
  ('KIT-CUPCAKE', 'Cupcake decorating kit',          450.00, 3),
  ('KIT-BREAD',   'Sourdough starter kit',           380.00, 3),
  ('NOZ-SET24',   '24-piece piping nozzle set',      150.00, 5),
  ('BAG-PIPE50',  'Disposable piping bags (50)',      60.00, 10),
  ('MAT-SILI',    'Silicone baking mat',             190.00, 5),
  ('BOX-CAKE10',  'Cake boxes, 10-inch (pack of 10)', 120.00, 8);

-- 50 customers spread evenly across the 4 channels, e.g. amazon-cust-001.
-- Lifetime value is a fixed spread from Rs 0 to Rs 4,900.
INSERT INTO customers (channel, external_id, lifetime_value)
SELECT ch.code,
       ch.code || '-cust-' || lpad(i::text, 3, '0'),
       ((i * 137) % 50) * 100
FROM generate_series(1, 50) AS i
JOIN LATERAL (
  SELECT (ARRAY['amazon','flipkart','website','counter'])[((i - 1) % 4) + 1] AS code
) ch ON true;

-- Opening stock is written as restock events, not typed into the snapshot,
-- so "stock = SUM(events)" holds from the first row.
-- TIN-8IN starts at 1 unit for the last-unit race demo.
INSERT INTO events (external_event_id, channel, product_id, type, quantity, occurred_at, payload)
SELECT 'seed-opening-' || p.sku, NULL, p.id, 'restock', s.qty, now(),
       jsonb_build_object('source', 'seed', 'note', 'opening stock')
FROM products p
JOIN (VALUES
  ('TIN-6IN', 12), ('TIN-8IN', 1),  ('TIN-LOAF', 15),  ('TIN-MUFFIN', 8),
  ('KIT-CUPCAKE', 6), ('KIT-BREAD', 4), ('NOZ-SET24', 20), ('BAG-PIPE50', 40),
  ('MAT-SILI', 10), ('BOX-CAKE10', 25)
) AS s(sku, qty) ON s.sku = p.sku;

-- Build the snapshot from the event log (the same query rebuilds it later).
INSERT INTO stock_snapshot (product_id, on_hand, reserved, last_event_id)
SELECT product_id, SUM(quantity), 0, MAX(id)
FROM events
GROUP BY product_id;

COMMIT;
