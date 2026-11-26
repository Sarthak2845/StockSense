// Channels that send signed webhooks. `counter` uses POST /pos/sale with a staff JWT instead.
const WEBHOOK_CHANNELS = ['amazon', 'flipkart', 'website'];

function loadConfig(env = process.env) {
  const webhookSecrets = {};
  for (const ch of WEBHOOK_CHANNELS) {
    const secret = env[`WEBHOOK_SECRET_${ch.toUpperCase()}`];
    if (secret) webhookSecrets[ch] = secret;
  }
  return {
    port: Number(env.PORT || 3000),
    redisUrl: env.REDIS_URL || 'redis://localhost:6379',
    amqpUrl: env.AMQP_URL || 'amqp://localhost',
    jwtSecret: env.JWT_SECRET,
    webhookSecrets,
    signatureToleranceSec: Number(env.SIGNATURE_TOLERANCE_SEC || 300),
    dedupTtlSec: Number(env.DEDUP_TTL_SEC || 86400),
    publishTimeoutMs: Number(env.PUBLISH_TIMEOUT_MS || 5000),
  };
}

function missingSecrets(config) {
  const missing = WEBHOOK_CHANNELS.filter((ch) => !config.webhookSecrets[ch]).map(
    (ch) => `WEBHOOK_SECRET_${ch.toUpperCase()}`,
  );
  if (!config.jwtSecret) missing.push('JWT_SECRET');
  return missing;
}

module.exports = { WEBHOOK_CHANNELS, loadConfig, missingSecrets };
