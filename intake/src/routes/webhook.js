const express = require('express');
const hmac = require('../hmac');
const { normalise } = require('../normalisers');
const { enqueue } = require('../enqueue');
const { PayloadError, RetryError } = require('../errors');
const { WEBHOOK_CHANNELS } = require('../config');

// Shared by /webhooks/:channel and /pos/sale.
async function normaliseAndEnqueue(res, channel, body, deps) {
  let events;
  try {
    events = normalise(channel, body);
  } catch (err) {
    if (err instanceof PayloadError) return res.status(400).json({ status: 'bad_payload', error: err.message });
    throw err;
  }

  try {
    const { queued, duplicates } = await enqueue(events, deps);
    if (queued === 0) return res.status(200).json({ status: 'duplicate', duplicates });
    return res.status(202).json({ status: 'queued', queued, duplicates });
  } catch (err) {
    if (err instanceof RetryError) {
      deps.log('error', err.message, { cause: err.cause && err.cause.message });
      return res.status(503).json({ status: 'retry' });
    }
    throw err;
  }
}

function webhookRouter({ redis, publisher, config, log }) {
  const router = express.Router();
  const deps = { redis, publisher, ttlSec: config.dedupTtlSec, log };

  // Raw body: the HMAC is computed over the exact bytes the channel sent.
  router.post('/webhooks/:channel', express.raw({ type: '*/*', limit: '100kb' }), async (req, res) => {
    const { channel } = req.params;
    if (!WEBHOOK_CHANNELS.includes(channel)) return res.status(404).json({ status: 'unknown_channel' });

    const rawBody = Buffer.isBuffer(req.body) ? req.body : Buffer.alloc(0);
    const ok = hmac.verify({
      secret: config.webhookSecrets[channel],
      timestamp: req.get('X-Timestamp'),
      signature: req.get('X-Signature'),
      rawBody,
      toleranceSec: config.signatureToleranceSec,
    });
    if (!ok) return res.status(401).json({ status: 'bad_signature' });

    let body;
    try {
      body = JSON.parse(rawBody.toString('utf8'));
    } catch {
      return res.status(400).json({ status: 'bad_payload', error: 'body is not valid JSON' });
    }

    return normaliseAndEnqueue(res, channel, body, deps);
  });

  return router;
}

module.exports = { webhookRouter, normaliseAndEnqueue };
