const express = require('express');
const { webhookRouter } = require('./routes/webhook');
const { posRouter } = require('./routes/pos');

function defaultLog(level, msg, fields = {}) {
  const line = JSON.stringify({ level, msg, ...fields, time: new Date().toISOString() });
  if (level === 'error' || level === 'warn') console.error(line);
  else console.log(line);
}

// Dependencies are injected so tests can pass in-memory fakes for Redis and RabbitMQ.
function createApp({ redis, publisher, config, log = defaultLog }) {
  const app = express();
  app.disable('x-powered-by');

  app.get('/health', (req, res) => res.json({ status: 'ok' }));
  app.use(webhookRouter({ redis, publisher, config, log }));
  app.use(posRouter({ redis, publisher, config, log }));

  app.use((req, res) => res.status(404).json({ status: 'not_found' }));

  // Body-parser errors (bad JSON, too large) carry a status; anything else is a 500.
  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    const status = err.status || err.statusCode || 500;
    if (status >= 500) log('error', 'unhandled error', { error: err.message, stack: err.stack });
    res.status(status).json({ status: status >= 500 ? 'error' : 'bad_request', error: status >= 500 ? undefined : err.message });
  });

  return app;
}

module.exports = { createApp, defaultLog };
