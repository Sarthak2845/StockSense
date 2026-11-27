"""Phase 1 gate: N simulated sales produce N events and correct stock.

Runs against the live `docker compose` stack:

    python simulators/phase1_gate.py                      # 100 sales, the PRD gate
    python simulators/phase1_gate.py --count 500 --duplicates 0.2

Steps: record stock -> send signed sales through intake (all 4 channels) ->
wait for the processor to drain the queue -> check every number. Exits 1 on any failure.

Expected stock is exact: for single-unit sales, a SKU with `before` units that
receives `sold` sales keeps min(sold, before) of them; the rest must become
conflicts with a cancel event, never negative stock.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import send  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
GATE_SKUS = ["BAG-PIPE50", "BOX-CAKE10", "NOZ-SET24", "TIN-LOAF", "TIN-6IN", "MAT-SILI"]


def psql(env: dict, sql: str) -> list[list[str]]:
    cmd = ["docker", "compose", "exec", "-T", "postgres", "psql",
           "-U", env.get("PG_USER", "stocksense"), "-d", env.get("PG_DB", "stocksense"),
           "-At", "-F", "\t", "-v", "ON_ERROR_STOP=1", "-c", sql]
    out = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=True,
                         env={**os.environ, "MSYS_NO_PATHCONV": "1"})
    return [line.split("\t") for line in out.stdout.splitlines() if line]


def stock_by_sku(env) -> dict[str, int]:
    rows = psql(env, "SELECT p.sku, s.on_hand FROM products p JOIN stock_snapshot s ON s.product_id = p.id")
    return {sku: int(n) for sku, n in rows}


def run_events(env, run: str, type_: str) -> int:
    # Every external ID and order ID from a run contains the run ID.
    return int(psql(env, f"""SELECT count(*) FROM events
        WHERE type = '{type_}' AND (external_event_id LIKE '%{run}%' OR order_id LIKE '%{run}%')""")[0][0])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=None, help="intake base URL (default http://127.0.0.1:$INTAKE_PORT)")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--rate", type=float, default=100)
    p.add_argument("--duplicates", type=float, default=0.0)
    p.add_argument("--skus", nargs="+", default=GATE_SKUS)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--timeout", type=float, default=60, help="seconds to wait for the queue to drain")
    args = p.parse_args()

    env = send.load_env()
    url = args.url or f"http://127.0.0.1:{env.get('INTAKE_PORT', '8080')}"
    run = f"g{uuid.uuid4().hex[:6]}"

    before = stock_by_sku(env)
    sender = send.Sender(url, env)
    load_args = argparse.Namespace(count=args.count, rate=args.rate, duplicates=args.duplicates,
                                   skus=args.skus, seed=args.seed, workers=32)
    t0 = time.perf_counter()
    sold = send.run_load(load_args, sender, run)
    sent_at = time.perf_counter()

    # Wait until every sale is in the event log.
    deadline = time.time() + args.timeout
    stored = 0
    while time.time() < deadline:
        stored = run_events(env, run, "sale")
        if stored >= args.count:
            break
        time.sleep(0.25)
    drained_at = time.perf_counter()

    after = stock_by_sku(env)
    cancels = run_events(env, run, "cancel")
    mismatched = psql(env, """SELECT count(*) FROM stock_snapshot s
        LEFT JOIN (SELECT product_id, SUM(quantity) t FROM events GROUP BY product_id) e USING (product_id)
        WHERE s.on_hand <> COALESCE(e.t, 0)""")[0][0]
    negative = psql(env, "SELECT count(*) FROM stock_snapshot WHERE on_hand < 0")[0][0]

    expected_cancels = sum(max(n - before[sku], 0) for sku, n in sold.items())
    stock_rows = []
    for sku in sorted(sold):
        expected = max(before[sku] - sold[sku], 0)
        stock_rows.append((sku, before[sku], sold[sku], expected, after[sku], expected == after[sku]))

    dupes_sent = sum(sender.statuses.values()) - args.count
    accepted = sender.statuses.get(202, 0)
    duplicate_replies = sender.statuses.get(200, 0)

    checks = [
        ("every unique sale accepted (202)", accepted == args.count, f"{accepted} of {args.count}"),
        ("every resend answered as duplicate (200)", duplicate_replies == dupes_sent, f"{duplicate_replies} of {dupes_sent}"),
        ("sales sent = sale events stored", stored == args.count, f"{stored} of {args.count}"),
        ("oversold sales became conflicts", cancels == expected_cancels, f"{cancels} cancels, expected {expected_cancels}"),
        ("stock correct for every SKU", all(r[-1] for r in stock_rows), ""),
        ("snapshot = SUM(events) for every product", mismatched == "0", f"{mismatched} mismatched"),
        ("no negative stock", negative == "0", f"{negative} negative"),
    ]

    print()
    print(f"{'SKU':<12}{'before':>8}{'sold':>6}{'expected':>10}{'after':>7}")
    for sku, b, s, e, a, ok in stock_rows:
        print(f"{sku:<12}{b:>8}{s:>6}{e:>10}{a:>7}  {'ok' if ok else 'WRONG'}")
    print()
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))

    lat = sorted(sender.latencies)
    print()
    print(f"run id {run}: sent in {sent_at - t0:.2f}s, all events stored {drained_at - sent_at:.2f}s after the last send")
    if lat:
        print(f"webhook latency ms: p50={lat[len(lat) // 2]:.1f} p95={lat[max(int(len(lat) * 0.95) - 1, 0)]:.1f}")

    passed = all(ok for _, ok, _ in checks)
    print("\nPHASE 1 GATE:", "PASSED" if passed else "FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
