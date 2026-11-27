const request = require('supertest');
const jwt = require('jsonwebtoken');
const { createApp } = require('../src/app');
const { testConfig, fakeRedis, fakePublisher, signedHeaders, samples, JWT_SECRET } = require('./helpers');

const silent = () => {};

function setup({ redis = fakeRedis(), publisher = fakePublisher(), config = testConfig() } = {}) {
  const app = createApp({ redis, publisher, config, log: silent });
  return { app, redis, publisher };
}

function postWebhook(app, channel, bodyObj, headerOpts) {
  const body = JSON.stringify(bodyObj);
  return request(app).post(`/webhooks/${channel}`).set(signedHeaders(channel, body, headerOpts)).send(body);
}

describe('POST /webhooks/:channel', () => {
  test('valid webhook is published and answered 202', async () => {
    const { app, publisher, redis } = setup();
    const res = await postWebhook(app, 'amazon', samples.amazon());
    expect(res.status).toBe(202);
    expect(res.body).toEqual({ status: 'queued', queued: 1, duplicates: 0 });
    expect(publisher.published).toHaveLength(1);
    expect(publisher.published[0]).toMatchObject({ channel: 'amazon', sku: 'TIN-8IN', quantity: -1 });
    expect(redis.store.has('evt:amazon:amz-7f3a91:1')).toBe(true);
  });

  test('the same webhook sent 5 times is published once', async () => {
    const { app, publisher } = setup();
    const statuses = [];
    for (let i = 0; i < 5; i += 1) statuses.push((await postWebhook(app, 'flipkart', samples.flipkart())).status);
    expect(statuses).toEqual([202, 200, 200, 200, 200]);
    expect(publisher.published).toHaveLength(1);
  });

  test('bad signature is 401 and nothing is published', async () => {
    const { app, publisher } = setup();
    const res = await postWebhook(app, 'website', samples.website(), { secret: 'wrong' });
    expect(res.status).toBe(401);
    expect(publisher.published).toHaveLength(0);
  });

  test('stale timestamp is 401', async () => {
    const { app } = setup();
    const res = await postWebhook(app, 'amazon', samples.amazon(), { timestamp: Math.floor(Date.now() / 1000) - 3600 });
    expect(res.status).toBe(401);
  });

  test('missing signature headers is 401', async () => {
    const { app } = setup();
    const res = await request(app).post('/webhooks/amazon').set('Content-Type', 'application/json').send('{}');
    expect(res.status).toBe(401);
  });

  test('signed but invalid JSON is 400', async () => {
    const { app } = setup();
    const body = '{not json';
    const res = await request(app).post('/webhooks/amazon').set(signedHeaders('amazon', body)).send(body);
    expect(res.status).toBe(400);
  });

  test('signed but wrong shape is 400', async () => {
    const { app, publisher } = setup();
    const res = await postWebhook(app, 'flipkart', { event_id: 'x', event_type: 'order_item_created' });
    expect(res.status).toBe(400);
    expect(res.body.status).toBe('bad_payload');
    expect(publisher.published).toHaveLength(0);
  });

  test('unknown channel and counter-as-webhook are 404', async () => {
    const { app } = setup();
    expect((await postWebhook(app, 'ebay', {}, { secret: 'x' })).status).toBe(404);
    expect((await postWebhook(app, 'counter', samples.counter(), { secret: 'x' })).status).toBe(404);
  });

  test('broker failure is 503 and the Redis key is released so a retry gets through', async () => {
    const redis = fakeRedis();
    const failing = setup({ redis, publisher: fakePublisher({ fail: true }) });
    const res = await postWebhook(failing.app, 'amazon', samples.amazon());
    expect(res.status).toBe(503);
    expect(redis.store.size).toBe(0);

    const healthy = setup({ redis });
    expect((await postWebhook(healthy.app, 'amazon', samples.amazon())).status).toBe(202);
    expect(healthy.publisher.published).toHaveLength(1);
  });

  test('Redis down: still publishes (Postgres is the permanent duplicate guard)', async () => {
    const { app, publisher } = setup({ redis: fakeRedis({ down: true }) });
    const res = await postWebhook(app, 'amazon', samples.amazon());
    expect(res.status).toBe(202);
    expect(publisher.published).toHaveLength(1);
  });
});

describe('POST /pos/sale', () => {
  const token = (role, secret = JWT_SECRET) => jwt.sign({ sub: 'staff-1', role }, secret, { algorithm: 'HS256', expiresIn: '1h' });

  test('staff token: sale queued', async () => {
    const { app, publisher } = setup();
    const res = await request(app).post('/pos/sale').set('Authorization', `Bearer ${token('staff')}`).send(samples.counter());
    expect(res.status).toBe(202);
    expect(publisher.published[0]).toMatchObject({ channel: 'counter', external_event_id: 'POS-0042', quantity: -1 });
  });

  test('resent POS sale (network retry) is a duplicate', async () => {
    const { app, publisher } = setup();
    const auth = `Bearer ${token('staff')}`;
    await request(app).post('/pos/sale').set('Authorization', auth).send(samples.counter());
    const res = await request(app).post('/pos/sale').set('Authorization', auth).send(samples.counter());
    expect(res.status).toBe(200);
    expect(publisher.published).toHaveLength(1);
  });

  test('no token, bad token or wrong role is rejected', async () => {
    const { app } = setup();
    expect((await request(app).post('/pos/sale').send(samples.counter())).status).toBe(401);
    expect((await request(app).post('/pos/sale').set('Authorization', `Bearer ${token('staff', 'wrong')}`).send(samples.counter())).status).toBe(401);
    expect((await request(app).post('/pos/sale').set('Authorization', `Bearer ${token('customer')}`).send(samples.counter())).status).toBe(403);
  });

  test('invalid JSON is 400', async () => {
    const { app } = setup();
    const res = await request(app)
      .post('/pos/sale')
      .set('Authorization', `Bearer ${token('owner')}`)
      .set('Content-Type', 'application/json')
      .send('{bad');
    expect(res.status).toBe(400);
  });
});

test('GET /health', async () => {
  const { app } = setup();
  const res = await request(app).get('/health');
  expect(res.status).toBe(200);
  expect(res.body).toEqual({ status: 'ok' });
});
