# Internal event contract

This is the shared contract between **intake** (which publishes events) and **processor** (which consumes them). If you need to change it, open a PR that updates this file, the JSON Schema and both services, and get a review from both owners.

- Machine-readable schema: [contracts/internal-event.schema.json](contracts/internal-event.schema.json) (JSON Schema 2020-12). Intake should validate against it before publishing, and processor tests should validate their fixtures against it.
- Source: PRD section 8.

## Message

```json
{
  "external_event_id": "amz-7f3a91",
  "channel": "amazon",
  "type": "sale",
  "sku": "TIN-8IN",
  "quantity": -1,
  "unit_price": 499.00,
  "order_id": "AMZ-ORD-8812",
  "customer_ref": "amazon-cust-001",
  "occurred_at": "2026-10-18T14:02:11.204Z"
}
```

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `external_event_id` | string | yes | The channel's own event ID. `(channel, external_event_id)` is the duplicate key. |
| `channel` | `amazon` \| `flipkart` \| `website` \| `counter` | yes | |
| `type` | `sale` \| `cancel` \| `return` | yes | Only channel-originated types go through the queue. `restock`, `adjust`, `reserve` and `release` are written by the API or the worker directly. |
| `sku` | string | yes | Must match `products.sku`. Uppercase letters, digits, hyphens. |
| `quantity` | non-zero integer | yes | **Signed** change to on-hand stock. `sale` ≤ -1; `cancel` and `return` ≥ 1. |
| `unit_price` | number (Rs) | for `sale` | Used for margin and conflict scoring. |
| `order_id` | string | for `sale`, `cancel`, `return` | The channel's order ID. |
| `customer_ref` | string or null | no | The channel's customer ID, matched to `customers(channel, external_id)`. Unknown customers are created by the worker. |
| `occurred_at` | ISO 8601 date-time with timezone | yes | Time at the channel. The worker sets `recorded_at` itself. |

No other fields are allowed (`additionalProperties: false`). The raw channel payload is not forwarded; if the worker needs to keep it, add an explicit field to this contract.

## Transport (RabbitMQ)

| Setting | Value |
| --- | --- |
| Queue | `events`, durable |
| Publishing | default exchange, routing key `events` |
| Delivery mode | persistent (`deliveryMode: 2`) |
| Content type | `application/json`, UTF-8 |
| Publisher | waits for the broker's **publisher confirm** before replying 202 |
| Consumer | manual ack, **only after the database transaction commits** |
| Queue arguments | `x-dead-letter-exchange: ""`, `x-dead-letter-routing-key: events.dlq` (both services must declare exactly these) |
| Dead letter | invalid messages and unknown SKUs are rejected to the durable `events.dlq`; transient DB errors are requeued. Counted retries come in Phase 2. |

Delivery is **at least once** and **not ordered**. The worker must handle:

- the same event arriving more than once (the `UNIQUE (channel, external_event_id)` constraint makes the second insert a no-op);
- a `cancel` or `return` arriving before its `sale`.

## Duplicate check (Redis, layer 1)

- Key: `evt:{channel}:{external_event_id}`
- Set with `SET key 1 NX EX 86400` (24 hours).
- If publishing fails, intake deletes the key and returns 503 so the channel retries.

Redis is only the fast first check. The permanent guard is the Postgres unique constraint (layer 2), because Redis keys expire and Redis can restart.
