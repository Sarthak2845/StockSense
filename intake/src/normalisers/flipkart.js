// Flipkart-style order item event (one webhook per order item):
// {
//   "event_id": "fk-91ab22",
//   "event_type": "order_item_created" | "order_item_cancelled" | "return_completed",
//   "timestamp": "2026-10-18T14:02:11.204Z",
//   "attributes": { "order_id": "OD4410", "order_item_id": "OI1", "sku": "TIN-8IN",
//                   "quantity": 1, "selling_price": 549, "customer_id": "flipkart-cust-002" }
// }
const { PayloadError } = require('../errors');
const u = require('./util');

const TYPES = {
  order_item_created: 'sale',
  order_item_cancelled: 'cancel',
  return_completed: 'return',
};

function normalise(body) {
  const kind = u.required(body, 'event_type');
  const type = TYPES[kind];
  if (!type) throw new PayloadError(`unsupported event_type: ${kind}`);

  return [
    {
      external_event_id: String(u.required(body, 'event_id')),
      channel: 'flipkart',
      type,
      sku: String(u.required(body, 'attributes.sku')),
      quantity: u.signedQuantity(type, u.positiveInt(u.required(body, 'attributes.quantity'), 'quantity')),
      unit_price: u.price(u.optional(body, 'attributes.selling_price'), 'selling_price'),
      order_id: String(u.required(body, 'attributes.order_id')),
      customer_ref: u.str(u.optional(body, 'attributes.customer_id')),
      occurred_at: u.isoTime(u.required(body, 'timestamp'), 'timestamp'),
    },
  ];
}

module.exports = { normalise };
