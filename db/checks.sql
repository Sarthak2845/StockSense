-- Sanity checks. Run any time:
--   docker compose exec -T postgres psql -U stocksense -d stocksense -f /db/checks.sql

-- 1. Row counts of reference data.
SELECT 'channels'  AS tbl, count(*) FROM channels
UNION ALL SELECT 'products',  count(*) FROM products
UNION ALL SELECT 'customers', count(*) FROM customers
UNION ALL SELECT 'events',    count(*) FROM events;

-- 2. Snapshot must equal SUM(events) per product. Any row here is a bug.
SELECT p.sku, s.on_hand AS snapshot, COALESCE(e.total, 0) AS from_events
FROM products p
LEFT JOIN stock_snapshot s ON s.product_id = p.id
LEFT JOIN (SELECT product_id, SUM(quantity) AS total FROM events GROUP BY product_id) e
       ON e.product_id = p.id
WHERE s.on_hand IS DISTINCT FROM COALESCE(e.total, 0);

-- 3. No product may be below zero (oversell).
SELECT product_id, on_hand FROM stock_snapshot WHERE on_hand < 0;
