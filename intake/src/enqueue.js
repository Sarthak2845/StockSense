const { RetryError } = require('./errors');

// Layer-1 duplicate check (Redis SET NX) + publish with broker confirm (PRD 9.1).
//
// - A key already present means the channel resent the event: skip it.
// - If publishing fails, the key is deleted so the channel's retry is not mistaken
//   for a duplicate, and RetryError (-> 503) tells the channel to retry.
// - If Redis itself is down we still publish. Redis is only a fast filter; the
//   permanent guard is the UNIQUE constraint in Postgres, so availability wins here.
async function enqueue(events, { redis, publisher, ttlSec, log = () => {} }) {
  let queued = 0;
  let duplicates = 0;

  for (const evt of events) {
    const key = `evt:${evt.channel}:${evt.external_event_id}`;

    let keySet = false;
    try {
      const isNew = await redis.set(key, '1', { NX: true, EX: ttlSec });
      if (!isNew) {
        duplicates += 1;
        continue;
      }
      keySet = true;
    } catch (err) {
      log('warn', 'redis unavailable, relying on Postgres duplicate guard', { error: err.message });
    }

    try {
      await publisher.publish(evt);
      queued += 1;
    } catch (err) {
      if (keySet) await redis.del(key).catch(() => {});
      throw new RetryError(`publish failed for ${key}`, err);
    }
  }

  return { queued, duplicates };
}

module.exports = { enqueue };
