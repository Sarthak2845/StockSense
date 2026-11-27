"""Channel simulator v1: sends signed sale webhooks for all 4 channels at a chosen rate.

Each channel uses its own JSON shape, as a real marketplace would. Standard library only.

    python simulators/send.py --count 100 --rate 50
    python simulators/send.py --count 1000 --rate 200 --duplicates 0.2     # chaos: resends
    python simulators/send.py --race TIN-8IN                                # chaos: last-unit race

Secrets come from the environment or the repo's .env file (WEBHOOK_SECRET_*, JWT_SECRET).
Every external event ID starts with a run ID, printed at the end, so a run can be
checked in the database afterwards.
"""

from __future__ import annotations

import argparse
import base64
import collections
import hashlib
import hmac
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

CHANNELS = ["amazon", "flipkart", "website", "counter"]
SKUS = ["TIN-6IN", "TIN-LOAF", "TIN-MUFFIN", "KIT-CUPCAKE", "KIT-BREAD",
        "NOZ-SET24", "BAG-PIPE50", "MAT-SILI", "BOX-CAKE10"]
PRICES = {"TIN-6IN": 349, "TIN-8IN": 499, "TIN-LOAF": 399, "TIN-MUFFIN": 549, "KIT-CUPCAKE": 799,
          "KIT-BREAD": 699, "NOZ-SET24": 299, "BAG-PIPE50": 129, "MAT-SILI": 349, "BOX-CAKE10": 249}
# Marketplaces charge more than our own site.
MARKUP = {"amazon": 1.10, "flipkart": 1.05, "website": 1.0, "counter": 0.95}


def load_env() -> dict:
    env = {}
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update(os.environ)
    return env


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def staff_jwt(secret: str) -> str:
    def b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"sub": "simulator", "role": "staff", "exp": int(time.time()) + 3600}).encode())
    sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{b64(sig)}"


def build_sale(channel: str, run: str, n: int, sku: str, customer: int) -> tuple[str, dict]:
    """Returns (path, body) in the channel's own webhook shape."""
    price = round(PRICES[sku] * MARKUP[channel], 2)
    cust = f"{channel}-cust-{customer:03d}"
    if channel == "amazon":
        return "/webhooks/amazon", {
            "NotificationType": "ORDER_CHANGE", "NotificationId": f"{run}-amz-{n}", "EventTime": now_iso(),
            "Payload": {"OrderChangeType": "OrderCreated", "AmazonOrderId": f"AMZ-{run}-{n}", "BuyerId": cust,
                        "OrderItems": [{"OrderItemId": "1", "SellerSKU": sku, "Quantity": 1,
                                        "ItemPrice": {"Amount": f"{price:.2f}", "CurrencyCode": "INR"}}]},
        }
    if channel == "flipkart":
        return "/webhooks/flipkart", {
            "event_id": f"{run}-fk-{n}", "event_type": "order_item_created", "timestamp": now_iso(),
            "attributes": {"order_id": f"OD-{run}-{n}", "order_item_id": "1", "sku": sku, "quantity": 1,
                           "selling_price": price, "customer_id": cust},
        }
    if channel == "website":
        return "/webhooks/website", {
            "webhook_id": f"{run}-web-{n}", "topic": "orders/create", "created_at": now_iso(),
            "order": {"id": n, "name": f"WEB-{run}-{n}", "customer": {"id": cust},
                      "line_items": [{"id": 1, "sku": sku, "quantity": 1, "price": f"{price:.2f}"}]},
        }
    return "/pos/sale", {"sale_id": f"POS-{run}-{n}", "sku": sku, "quantity": 1, "price": price,
                         "customer_ref": cust, "sold_at": now_iso()}


class Sender:
    def __init__(self, base_url: str, env: dict):
        self.base_url = base_url.rstrip("/")
        self.secrets = {ch: env.get(f"WEBHOOK_SECRET_{ch.upper()}", "") for ch in CHANNELS[:3]}
        self.token = staff_jwt(env.get("JWT_SECRET", ""))
        self.statuses = collections.Counter()
        self.latencies: list[float] = []
        self.lock = threading.Lock()

    def send(self, channel: str, path: str, body: dict) -> int:
        raw = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if channel == "counter":
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            ts = str(int(time.time()))
            sig = hmac.new(self.secrets[channel].encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
            headers.update({"X-Timestamp": ts, "X-Signature": f"sha256={sig}"})
        req = urllib.request.Request(self.base_url + path, data=raw, headers=headers, method="POST")
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
        except OSError:
            status = 0  # connection error
        with self.lock:
            self.statuses[status] += 1
            self.latencies.append((time.perf_counter() - start) * 1000)
        return status


def run_load(args, sender: Sender, run: str) -> collections.Counter:
    rng = random.Random(args.seed)
    plan = []
    for n in range(1, args.count + 1):
        channel = rng.choice(CHANNELS)
        sku = rng.choice(args.skus)
        plan.append((channel, *build_sale(channel, run, n, sku, rng.randint(1, 50))))
        if rng.random() < args.duplicates:   # chaos: resend the exact same webhook
            plan.append(plan[-1])
    unique_per_sku = collections.Counter()
    seen = set()
    for ch, path, body in plan:
        key = id(body)
        if key not in seen:
            seen.add(key)
            unique_per_sku[_sku_of(body)] += 1

    interval = 1.0 / args.rate if args.rate > 0 else 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        start = time.perf_counter()
        for i, (ch, path, body) in enumerate(plan):
            if interval:
                delay = start + i * interval - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
            pool.submit(sender.send, ch, path, body)
    print(f"sent {len(plan)} requests ({args.count} unique sales, {len(plan) - args.count} duplicates)")
    return unique_per_sku


def run_race(args, sender: Sender, run: str) -> None:
    """Chaos: one sale per channel for the same SKU, fired at the same instant."""
    barrier = threading.Barrier(len(CHANNELS))
    rng = random.Random(args.seed)

    def fire(i, ch):
        path, body = build_sale(ch, run, i, args.race, rng.randint(1, 50))
        barrier.wait()
        sender.send(ch, path, body)

    threads = [threading.Thread(target=fire, args=(i, ch)) for i, ch in enumerate(CHANNELS, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print(f"race: {len(CHANNELS)} channels sold {args.race} at the same instant")


def _sku_of(body: dict) -> str:
    if "Payload" in body:
        return body["Payload"]["OrderItems"][0]["SellerSKU"]
    if "attributes" in body:
        return body["attributes"]["sku"]
    if "order" in body:
        return body["order"]["line_items"][0]["sku"]
    return body["sku"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://localhost:8080", help="intake base URL")
    p.add_argument("--count", type=int, default=100, help="unique sales to send")
    p.add_argument("--rate", type=float, default=50, help="requests per second (0 = as fast as possible)")
    p.add_argument("--duplicates", type=float, default=0.0, help="probability of resending each webhook")
    p.add_argument("--skus", nargs="+", default=SKUS)
    p.add_argument("--race", metavar="SKU", help="fire a last-unit race for this SKU instead of a load run")
    p.add_argument("--workers", type=int, default=32)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--run-id", default=None)
    args = p.parse_args()

    run = args.run_id or f"r{uuid.uuid4().hex[:6]}"
    sender = Sender(args.url, load_env())

    if args.race:
        run_race(args, sender, run)
    else:
        per_sku = run_load(args, sender, run)
        print("unique sales per SKU:", dict(sorted(per_sku.items())))

    lat = sorted(sender.latencies)
    p95 = lat[int(len(lat) * 0.95) - 1] if lat else 0
    print(f"run id: {run}")
    print(f"responses: {dict(sorted(sender.statuses.items()))}  (202 queued, 200 duplicate)")
    print(f"latency ms: p50={lat[len(lat) // 2]:.1f} p95={p95:.1f} max={lat[-1]:.1f}" if lat else "no responses")


if __name__ == "__main__":
    main()
