const amqp = require('amqplib');

// Topology shared with the processor (docs/event-contract.md). Both sides declare it;
// the arguments must match exactly or RabbitMQ refuses the declaration.
const QUEUE = 'events';
const DEAD_LETTER_QUEUE = 'events.dlq';
const QUEUE_ARGS = {
  'x-dead-letter-exchange': '',
  'x-dead-letter-routing-key': DEAD_LETTER_QUEUE,
};

// RabbitMQ publisher using a confirm channel. publish() resolves only once the
// broker has confirmed the message is stored, which is what lets intake reply 202.
// Reconnects lazily: if the connection drops, the next publish opens a new one.
class Publisher {
  constructor(url, { timeoutMs = 5000, log = () => {} } = {}) {
    this.url = url;
    this.timeoutMs = timeoutMs;
    this.log = log;
    this.channelPromise = null;
    this.connection = null;
  }

  connect() {
    if (this.channelPromise) return this.channelPromise;

    const reset = () => {
      this.channelPromise = null;
      this.connection = null;
    };

    this.channelPromise = (async () => {
      const conn = await amqp.connect(this.url);
      conn.on('error', (err) => this.log('error', 'amqp connection error', { error: err.message }));
      conn.on('close', reset);

      const ch = await conn.createConfirmChannel();
      ch.on('error', (err) => this.log('error', 'amqp channel error', { error: err.message }));
      ch.on('close', reset);

      await ch.assertQueue(DEAD_LETTER_QUEUE, { durable: true });
      await ch.assertQueue(QUEUE, { durable: true, arguments: QUEUE_ARGS });
      this.connection = conn;
      return ch;
    })();

    this.channelPromise.catch(reset);
    return this.channelPromise;
  }

  async publish(evt) {
    const ch = await this.connect();
    const body = Buffer.from(JSON.stringify(evt));

    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('publisher confirm timed out')), this.timeoutMs);
      ch.sendToQueue(
        QUEUE,
        body,
        {
          persistent: true,
          contentType: 'application/json',
          messageId: `${evt.channel}:${evt.external_event_id}`,
          timestamp: Math.floor(Date.now() / 1000),
        },
        (err) => {
          clearTimeout(timer);
          if (err) reject(err);
          else resolve();
        },
      );
    });
  }

  async close() {
    if (this.connection) await this.connection.close().catch(() => {});
  }
}

module.exports = { Publisher, QUEUE, DEAD_LETTER_QUEUE, QUEUE_ARGS };
