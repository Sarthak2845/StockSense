const { sign } = require('../src/hmac');

const SECRETS = { amazon: 'test-amazon', flipkart: 'test-flipkart', website: 'test-website' };
const JWT_SECRET = 'test-jwt';

function testConfig(overrides = {}) {
  return {
    webhookSecrets: SECRETS,
    jwtSecret: JWT_SECRET,
    signatureToleranceSec: 300,
    dedupTtlSec: 86400,
    ...overrides,
  };
}

// In-memory stand-in for node-redis: set(key, val, {NX}) returns 'OK' or null.
function fakeRedis({ down = false } = {}) {
  const store = new Map();
  return {
    store,
    async set(key, val, opts = {}) {
      if (down) throw new Error('redis down');
      if (opts.NX && store.has(key)) return null;
      store.set(key, val);
      return 'OK';
    },
    async del(key) {
      store.delete(key);
      return 1;
    },
  };
}

function fakePublisher({ fail = false } = {}) {
  return {
    published: [],
    async publish(evt) {
      if (fail) throw new Error('broker unavailable');
      this.published.push(evt);
    },
  };
}

function signedHeaders(channel, body, { secret = SECRETS[channel], timestamp } = {}) {
  const ts = String(timestamp ?? Math.floor(Date.now() / 1000));
  return {
    'Content-Type': 'application/json',
    'X-Timestamp': ts,
    'X-Signature': `sha256=${sign(secret, ts, body)}`,
  };
}

const samples = {
  amazon: () => ({
    NotificationType: 'ORDER_CHANGE',
    NotificationId: 'amz-7f3a91',
    EventTime: '2026-10-18T14:02:11.204Z',
    Payload: {
      OrderChangeType: 'OrderCreated',
      AmazonOrderId: 'AMZ-ORD-8812',
      BuyerId: 'amazon-cust-001',
      OrderItems: [
        { OrderItemId: '1', SellerSKU: 'TIN-8IN', Quantity: 1, ItemPrice: { Amount: '499.00', CurrencyCode: 'INR' } },
      ],
    },
  }),
  flipkart: () => ({
    event_id: 'fk-91ab22',
    event_type: 'order_item_created',
    timestamp: '2026-10-18T14:02:11.204Z',
    attributes: {
      order_id: 'OD4410',
      order_item_id: 'OI1',
      sku: 'TIN-8IN',
      quantity: 1,
      selling_price: 549,
      customer_id: 'flipkart-cust-002',
    },
  }),
  website: () => ({
    webhook_id: 'web-55e1',
    topic: 'orders/create',
    created_at: '2026-10-18T14:02:11.204Z',
    order: {
      id: 4521,
      name: 'WEB-4521',
      customer: { id: 'website-cust-003' },
      line_items: [{ id: 1, sku: 'TIN-8IN', quantity: 1, price: '499.00' }],
    },
  }),
  counter: () => ({ sale_id: 'POS-0042', sku: 'TIN-8IN', quantity: 1, price: 450, sold_at: '2026-10-18T14:02:11.204Z' }),
};

module.exports = { SECRETS, JWT_SECRET, testConfig, fakeRedis, fakePublisher, signedHeaders, samples };
