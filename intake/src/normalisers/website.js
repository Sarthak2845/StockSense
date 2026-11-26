// Own-website order webhook (Shopify-like shape):
// {
//   "webhook_id": "web-55e1",
//   "topic": "orders/create" | "orders/cancelled" | "refunds/create",
//   "created_at": "2026-10-18T14:02:11.204Z",
//   "order": { "id": 4521, "name": "WEB-4521", "customer": { "id": "website-cust-003" },
//              "line_items": [{ "id": 1, "sku": "TIN-8IN", "quantity": 1, "price": "499.00" }] }
// }
// One internal event per line item; external_event_id = `${webhook_id}:${line_item.id}`.
const { PayloadError } = require('../errors');
const u = require('./util');

const TYPES = { 'orders/create': 'sale', 'orders/cancelled': 'cancel', 'refunds/create': 'return' };

function normalise(body) {
  const topic = u.required(body, 'topic');
  const type = TYPES[topic];
  if (!type) throw new PayloadError(`unsupported topic: ${topic}`);

  const webhookId = String(u.required(body, 'webhook_id'));
  const items = u.required(body, 'order.line_items');
  if (!Array.isArray(items) || items.length === 0) throw new PayloadError('line_items must be a non-empty array');

  const base = {
    channel: 'website',
    type,
    order_id: String(u.required(body, 'order.name')),
    customer_ref: u.str(u.optional(body, 'order.customer.id')),
    occurred_at: u.isoTime(u.required(body, 'created_at'), 'created_at'),
  };

  return items.map((item, i) => ({
    external_event_id: `${webhookId}:${u.optional(item, 'id') ?? i + 1}`,
    ...base,
    sku: String(u.required(item, 'sku')),
    quantity: u.signedQuantity(type, u.positiveInt(u.required(item, 'quantity'), 'quantity')),
    unit_price: u.price(u.optional(item, 'price'), 'price'),
  }));
}

module.exports = { normalise };
