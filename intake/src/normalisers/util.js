const { PayloadError } = require('../errors');

function get(obj, pathStr) {
  return pathStr.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function required(obj, pathStr) {
  const v = get(obj, pathStr);
  if (v === undefined || v === null || v === '') throw new PayloadError(`missing field: ${pathStr}`);
  return v;
}

function optional(obj, pathStr) {
  const v = get(obj, pathStr);
  return v === undefined || v === '' ? null : v;
}

function positiveInt(v, name) {
  const n = Number(v);
  if (!Number.isInteger(n) || n < 1) throw new PayloadError(`${name} must be a positive integer`);
  return n;
}

function price(v, name) {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  if (!Number.isFinite(n) || n < 0) throw new PayloadError(`${name} must be a non-negative number`);
  return Math.round(n * 100) / 100;
}

function isoTime(v, name) {
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) throw new PayloadError(`${name} must be a date-time`);
  return d.toISOString();
}

// Contract rule: sale reduces stock (negative), cancel and return add it back (positive).
function signedQuantity(type, units) {
  return type === 'sale' ? -units : units;
}

function str(v) {
  return v === null || v === undefined ? null : String(v);
}

module.exports = { get, required, optional, positiveInt, price, isoTime, signedQuantity, str };
