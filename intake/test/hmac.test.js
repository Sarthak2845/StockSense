const { sign, verify } = require('../src/hmac');

const secret = 's3cret';
const body = Buffer.from('{"a":1}');
const now = 1_760_000_000_000;
const ts = String(now / 1000);

const base = () => ({ secret, timestamp: ts, signature: sign(secret, ts, body), rawBody: body, toleranceSec: 300, nowMs: now });

describe('hmac.verify', () => {
  test('accepts a correct signature, with or without the sha256= prefix', () => {
    expect(verify(base())).toBe(true);
    expect(verify({ ...base(), signature: `sha256=${sign(secret, ts, body)}` })).toBe(true);
  });

  test('rejects a tampered body', () => {
    expect(verify({ ...base(), rawBody: Buffer.from('{"a":2}') })).toBe(false);
  });

  test('rejects the wrong secret', () => {
    expect(verify({ ...base(), signature: sign('other', ts, body) })).toBe(false);
  });

  test('rejects a timestamp outside the tolerance window (replay)', () => {
    const old = String(now / 1000 - 301);
    expect(verify({ ...base(), timestamp: old, signature: sign(secret, old, body) })).toBe(false);
  });

  test('rejects missing or malformed inputs without throwing', () => {
    expect(verify({ ...base(), signature: undefined })).toBe(false);
    expect(verify({ ...base(), timestamp: 'abc' })).toBe(false);
    expect(verify({ ...base(), signature: 'zz-not-hex' })).toBe(false);
    expect(verify({ ...base(), secret: undefined })).toBe(false);
  });
});
