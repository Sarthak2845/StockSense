const crypto = require('crypto');

// Signature = hex(HMAC-SHA256(secret, `${timestamp}.${rawBody}`)), sent as
//   X-Timestamp: <unix seconds>
//   X-Signature: sha256=<hex>   (the "sha256=" prefix is optional)
// Signing the timestamp with the body stops an old captured request being replayed
// after the tolerance window.

function sign(secret, timestamp, rawBody) {
  return crypto
    .createHmac('sha256', secret)
    .update(`${timestamp}.`)
    .update(rawBody)
    .digest('hex');
}

function verify({ secret, timestamp, signature, rawBody, toleranceSec, nowMs = Date.now() }) {
  if (!secret || !timestamp || !signature) return false;
  if (!/^\d{1,12}$/.test(timestamp)) return false;
  if (Math.abs(nowMs / 1000 - Number(timestamp)) > toleranceSec) return false;

  const expected = Buffer.from(sign(secret, timestamp, rawBody), 'hex');
  const given = Buffer.from(String(signature).replace(/^sha256=/, ''), 'hex');
  return given.length === expected.length && crypto.timingSafeEqual(given, expected);
}

module.exports = { sign, verify };
