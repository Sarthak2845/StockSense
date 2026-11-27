"""StockSense event processor: consumes the `events` queue and applies each event.

Ack rules (PRD FR-3.1):
- ack only after the database transaction has committed;
- invalid messages and unknown SKUs are rejected without requeue -> `events.dlq`;
- transient database errors are requeued, so the event is retried, never lost.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time

import pika
import psycopg
from psycopg import errors as pg_errors

from conflicts import ScoringConfig
from contract import InvalidEvent, parse_event
from store import UnknownProduct, process_event

QUEUE = "events"
DEAD_LETTER_QUEUE = "events.dlq"
# Must match intake/src/queue.js exactly, or RabbitMQ refuses the declaration.
QUEUE_ARGS = {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": DEAD_LETTER_QUEUE}

TRANSIENT_DB_ERRORS = (
    psycopg.OperationalError,
    pg_errors.DeadlockDetected,
    pg_errors.SerializationFailure,
    pg_errors.LockNotAvailable,
)

log = logging.getLogger("processor")


def retry(name: str, fn, attempts: int = 30, delay: float = 2.0):
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - startup only
            if i == attempts:
                raise
            log.warning("%s not ready (attempt %d): %s", name, i, e)
            time.sleep(delay)


class Worker:
    def __init__(self, database_url: str, amqp_url: str, cfg: ScoringConfig, prefetch: int = 20):
        self.database_url = database_url
        self.amqp_url = amqp_url
        self.cfg = cfg
        self.prefetch = prefetch
        self.db: psycopg.Connection | None = None
        self.channel = None

    def connect_db(self) -> psycopg.Connection:
        if self.db is None or self.db.closed:
            self.db = psycopg.connect(self.database_url, autocommit=True)
        return self.db

    def on_message(self, ch, method, properties, body: bytes) -> None:
        try:
            evt = parse_event(body)
            outcome = process_event(self.connect_db(), evt, self.cfg)
        except (InvalidEvent, UnknownProduct) as e:
            log.error("rejecting message to %s: %s", DEAD_LETTER_QUEUE, e)
            ch.basic_nack(method.delivery_tag, requeue=False)
            return
        except TRANSIENT_DB_ERRORS as e:
            log.warning("transient database error, requeueing: %s", e)
            if isinstance(e, psycopg.OperationalError) and self.db is not None:
                self.db.close()
            time.sleep(0.5)
            ch.basic_nack(method.delivery_tag, requeue=True)
            return
        except Exception:
            log.exception("unexpected error, moving message to %s", DEAD_LETTER_QUEUE)
            ch.basic_nack(method.delivery_tag, requeue=False)
            return

        ch.basic_ack(method.delivery_tag)  # only reached after COMMIT
        log.info(
            "%s %s:%s %s qty=%s -> %s on_hand=%s%s",
            outcome.status, evt["channel"], evt["external_event_id"], evt["sku"], evt["quantity"],
            outcome.event_id, outcome.on_hand,
            f" conflicts={outcome.conflict_ids}" if outcome.conflict_ids else "",
        )

    def run(self) -> None:
        retry("postgres", self.connect_db)
        params = pika.URLParameters(self.amqp_url)
        params.heartbeat = 30
        connection = retry("rabbitmq", lambda: pika.BlockingConnection(params))
        self.channel = connection.channel()
        self.channel.queue_declare(DEAD_LETTER_QUEUE, durable=True)
        self.channel.queue_declare(QUEUE, durable=True, arguments=QUEUE_ARGS)
        self.channel.basic_qos(prefetch_count=self.prefetch)
        self.channel.basic_consume(QUEUE, self.on_message, auto_ack=False)

        def stop(signum, _frame):
            log.info("signal %s: stopping after the current message", signum)
            self.channel.stop_consuming()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        log.info("consuming from %s", QUEUE)
        try:
            self.channel.start_consuming()
        finally:
            if connection.is_open:
                connection.close()
            if self.db is not None:
                self.db.close()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    database_url = os.environ.get("DATABASE_URL")
    amqp_url = os.environ.get("AMQP_URL")
    if not database_url or not amqp_url:
        log.error("DATABASE_URL and AMQP_URL must be set")
        sys.exit(1)
    cfg = ScoringConfig.load(os.environ.get("SCORING_CONFIG"))
    Worker(database_url, amqp_url, cfg, prefetch=int(os.environ.get("PREFETCH", "20"))).run()


if __name__ == "__main__":
    main()
