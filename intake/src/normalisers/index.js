const { assertValidEvent } = require('../contract');

const normalisers = {
  amazon: require('./amazon').normalise,
  flipkart: require('./flipkart').normalise,
  website: require('./website').normalise,
  counter: require('./counter').normalise,
};

// Returns the internal events for one channel payload, each checked against the contract.
// Throws PayloadError for anything malformed.
function normalise(channel, body, opts) {
  const fn = normalisers[channel];
  if (!fn) throw new Error(`no normaliser for channel ${channel}`);
  if (body === null || typeof body !== 'object' || Array.isArray(body)) {
    const { PayloadError } = require('../errors');
    throw new PayloadError('body must be a JSON object');
  }
  return fn(body, opts).map(assertValidEvent);
}

module.exports = { normalise, channels: Object.keys(normalisers) };
