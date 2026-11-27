// Amazon-style order notification (shape modelled on SP-API ORDER_CHANGE notifications):
// {
//   "NotificationType": "ORDER_CHANGE",
//   "NotificationId": "amz-7f3a91",
//   "EventTime": "2026-10-18T14:02:11.204Z",
//   "Payload": {
//     "OrderChangeType": "OrderCreated" | "OrderCanceled" | "OrderReturned",
//     "AmazonOrderId": "AMZ-ORD-8812",
//     "BuyerId": "amazon-cust-001",
//     "OrderItems": [{ "OrderItemId": "1", "SellerSKU": "TIN-8IN", "Quantity": 1,
//                      "ItemPrice": { "Amount": "499.00", "CurrencyCode": "INR" } }]
//   }
// }
// One internal event per order item; external_event_id = `${NotificationId}:${OrderItemId}`.
const { PayloadError } = require('../errors');
const u = require('./util');

const TYPES = { OrderCreated: 'sale', OrderCanceled: 'cancel', OrderReturned: 'return' };

function normalise(body) {
  const kind = u.required(body, 'Payload.OrderChangeType');
  const type = TYPES[kind];
  if (!type) throw new PayloadError(`unsupported OrderChangeType: ${kind}`);

  const notificationId = String(u.required(body, 'NotificationId'));
  const items = u.required(body, 'Payload.OrderItems');
  if (!Array.isArray(items) || items.length === 0) throw new PayloadError('OrderItems must be a non-empty array');

  const base = {
    channel: 'amazon',
    type,
    order_id: String(u.required(body, 'Payload.AmazonOrderId')),
    customer_ref: u.str(u.optional(body, 'Payload.BuyerId')),
    occurred_at: u.isoTime(u.required(body, 'EventTime'), 'EventTime'),
  };

  return items.map((item, i) => ({
    external_event_id: `${notificationId}:${u.optional(item, 'OrderItemId') ?? i + 1}`,
    ...base,
    sku: String(u.required(item, 'SellerSKU')),
    quantity: u.signedQuantity(type, u.positiveInt(u.required(item, 'Quantity'), 'Quantity')),
    unit_price: u.price(u.optional(item, 'ItemPrice.Amount'), 'ItemPrice.Amount'),
  }));
}

module.exports = { normalise };
