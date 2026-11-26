const { createClient } = require('redis');
const { createApp, defaultLog: log } = require('./app');
const { loadConfig, missingSecrets } = require('./config');
const { Publisher } = require('./queue');

async function connectWithRetry(name, fn, attempts = 30, delayMs = 2000) {
  for (let i = 1; ; i += 1) {
    try {
      return await fn();
    } catch (err) {
      if (i >= attempts) throw err;
      log('warn', `${name} not ready, retrying`, { attempt: i, error: err.message });
      await new Promise((r) => setTimeout(r, delayMs));
    }
  }
}

async function main() {
  const config = loadConfig();
  const missing = missingSecrets(config);
  if (missing.length) {
    log('error', 'missing required environment variables', { missing });
    process.exit(1);
  }

  // disableOfflineQueue: when Redis is down, commands fail fast instead of hanging,
  // so enqueue() can fall back to the Postgres duplicate guard.
  const redis = createClient({ url: config.redisUrl, disableOfflineQueue: true });
  redis.on('error', (err) => log('error', 'redis error', { error: err.message }));
  await connectWithRetry('redis', () => redis.connect());

  const publisher = new Publisher(config.amqpUrl, { timeoutMs: config.publishTimeoutMs, log });
  await connectWithRetry('rabbitmq', () => publisher.connect());

  const app = createApp({ redis, publisher, config, log });
  const server = app.listen(config.port, () => log('info', 'intake listening', { port: config.port }));

  const shutdown = (signal) => {
    log('info', 'shutting down', { signal });
    server.close(async () => {
      await publisher.close();
      await redis.quit().catch(() => {});
      process.exit(0);
    });
    setTimeout(() => process.exit(1), 10000).unref();
  };
  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);
}

main().catch((err) => {
  log('error', 'intake failed to start', { error: err.message });
  process.exit(1);
});
