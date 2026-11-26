// Shop counter sale from POST /pos/sale (staff JWT):
// { "sale_id": "POS-20261018-0042", "sku": "TIN-8IN", "quantity": 1, "price": 450,
//   "customer_ref": null, "sold_at": "2026-10-18T14:02:11.204Z" }
// The POS app generates sale_id once and resends the same body if the network drops,
// so retries are de-duplicated. sold_at defaults to now.
const u = require('./util');

function normalise(body, { now = new Date() } = {}) {
  const saleId = String(u.required(body, 'sale_id'));
  const soldAt = u.optional(body, 'sold_at');
  return [
    {
      external_event_id: saleId,
      channel: 'counter',
      type: 'sale',
      sku: String(u.required(body, 'sku')),
      quantity: u.signedQuantity('sale', u.positiveInt(u.optional(body, 'quantity') ?? 1, 'quantity')),
      unit_price: u.price(u.required(body, 'price'), 'price'),
      order_id: saleId,
      customer_ref: u.str(u.optional(body, 'customer_ref')),
      occurred_at: soldAt ? u.isoTime(soldAt, 'sold_at') : now.toISOString(),
    },
  ];
}

module.exports = { normalise };
