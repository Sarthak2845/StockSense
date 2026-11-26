// The payload is malformed or does not match the contract -> 400. The channel should not retry.
class PayloadError extends Error {
  constructor(message) {
    super(message);
    this.name = 'PayloadError';
  }
}

// Publishing failed -> 503. The channel should retry later.
class RetryError extends Error {
  constructor(message, cause) {
    super(message);
    this.name = 'RetryError';
    this.cause = cause;
  }
}

module.exports = { PayloadError, RetryError };
