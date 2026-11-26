const { normalise } = require('../src/normalisers');
const { PayloadError } = require('../src/errors');
const { samples } = require('./helpers');

describe('normalisers produce the internal contract', () => {
  test('amazon sale', () => {
    expect(normalise('amazon', samples.amazon())).toEqual([
      {
        external_event_id: 'amz-7f3a91:1',
        channel: 'amazon',
        type: 'sale',
        sku: 'TIN-8IN',
        quantity: -1,
        unit_price: 499,
        order_id: 'AMZ-ORD-8812',
        customer_ref: 'amazon-cust-001',
        occurred_at: '2026-10-18T14:02:11.204Z',
      },
    ]);
  });

  test('amazon multi-item order gives one event per item with distinct ids', () => {
    const body = samples.amazon();
    body.Payload.OrderItems.push({ OrderItemId: '2', SellerSKU: 'NOZ-SET24', Quantity: 3, ItemPrice: { Amount: '299' } });
    const events = normalise('amazon', body);
    expect(events.map((e) => [e.external_event_id, e.sku, e.quantity])).toEqual([
      ['amz-7f3a91:1', 'TIN-8IN', -1],
      ['amz-7f3a91:2', 'NOZ-SET24', -3],
    ]);
  });

  test('flipkart sale', () => {
    const [evt] = normalise('flipkart', samples.flipkart());
    expect(evt).toMatchObject({ external_event_id: 'fk-91ab22', channel: 'flipkart', type: 'sale', quantity: -1, unit_price: 549, order_id: 'OD4410' });
  });

  test('website sale uses the order name as order_id', () => {
    const [evt] = normalise('website', samples.website());
    expect(evt).toMatchObject({ external_event_id: 'web-55e1:1', channel: 'website', order_id: 'WEB-4521', quantity: -1, customer_ref: 'website-cust-003' });
  });

  test('counter sale defaults quantity to 1 and sold_at to now', () => {
    const now = new Date('2026-11-26T10:00:00Z');
    const [evt] = normalise('counter', { sale_id: 'POS-1', sku: 'BAG-PIPE50', price: 99 }, { now });
    expect(evt).toMatchObject({ channel: 'counter', type: 'sale', quantity: -1, order_id: 'POS-1', occurred_at: now.toISOString() });
  });

  test.each([
    ['amazon', (b) => (b.Payload.OrderChangeType = 'OrderCanceled'), 'cancel'],
    ['amazon', (b) => (b.Payload.OrderChangeType = 'OrderReturned'), 'return'],
    ['flipkart', (b) => (b.event_type = 'order_item_cancelled'), 'cancel'],
    ['flipkart', (b) => (b.event_type = 'return_completed'), 'return'],
    ['website', (b) => (b.topic = 'orders/cancelled'), 'cancel'],
    ['website', (b) => (b.topic = 'refunds/create'), 'return'],
  ])('%s %s -> %s has positive quantity', (channel, mutate, type) => {
    const body = samples[channel]();
    mutate(body);
    const [evt] = normalise(channel, body);
    expect(evt.type).toBe(type);
    expect(evt.quantity).toBe(1);
  });
});

describe('normalisers reject bad payloads', () => {
  test.each([
    ['amazon', (b) => delete b.NotificationId],
    ['amazon', (b) => (b.Payload.OrderItems = [])],
    ['amazon', (b) => (b.Payload.OrderChangeType = 'OrderShipped')],
    ['flipkart', (b) => (b.attributes.quantity = 0)],
    ['flipkart', (b) => (b.attributes.quantity = 1.5)],
    ['flipkart', (b) => (b.timestamp = 'yesterday')],
    ['website', (b) => (b.order.line_items[0].sku = 'lower-case')],
    ['website', (b) => (b.order.line_items[0].price = '-5')],
    ['counter', (b) => delete b.price],
  ])('%s: %s', (channel, mutate) => {
    const body = samples[channel]();
    mutate(body);
    expect(() => normalise(channel, body)).toThrow(PayloadError);
  });

  test('non-object body', () => {
    expect(() => normalise('flipkart', [1, 2])).toThrow(PayloadError);
    expect(() => normalise('flipkart', null)).toThrow(PayloadError);
  });
});
