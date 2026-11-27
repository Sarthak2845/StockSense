const express = require('express');
const jwt = require('jsonwebtoken');
const { normaliseAndEnqueue } = require('./webhook');

const POS_ROLES = new Set(['staff', 'owner']);

function requireStaff(jwtSecret) {
  return (req, res, next) => {
    const header = req.get('Authorization') || '';
    const [scheme, token] = header.split(' ');
    if (scheme !== 'Bearer' || !token || !jwtSecret) return res.status(401).json({ status: 'unauthorized' });
    try {
      const claims = jwt.verify(token, jwtSecret, { algorithms: ['HS256'] });
      if (!POS_ROLES.has(claims.role)) return res.status(403).json({ status: 'forbidden' });
      req.user = claims;
      return next();
    } catch {
      return res.status(401).json({ status: 'unauthorized' });
    }
  };
}

function posRouter({ redis, publisher, config, log }) {
  const router = express.Router();
  const deps = { redis, publisher, ttlSec: config.dedupTtlSec, log };

  router.post('/pos/sale', requireStaff(config.jwtSecret), express.json({ limit: '16kb' }), (req, res) =>
    normaliseAndEnqueue(res, 'counter', req.body, deps),
  );

  return router;
}

module.exports = { posRouter, requireStaff };
