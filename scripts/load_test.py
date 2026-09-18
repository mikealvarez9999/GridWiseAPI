"""Concurrent load test: reports p50/p95 latency and failures.

Usage:  uv run python scripts/load_test.py --base-url https://<host> --concurrency 5 --rounds 2
"""
import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


async def one(client: httpx.AsyncClient, url: str, payload: dict) -> tuple[float, int]:
    t0 = time.perf_counter()
    try:
        r = await client.post(url, json=payload)
        return time.perf_counter() - t0, r.status_code
    except Exception:
        return time.perf_counter() - t0, 0


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--fresh-notes", action="store_true",
                    help="use distinct paraphrase-bank notes per request so the interpretation cache cannot help")
    args = ap.parse_args()

    cases = json.loads((ROOT / "samples" / "public_cases.json").read_text())["cases"]
    payloads = [c["input"] for c in cases]
    if args.fresh_notes:
        bank = json.loads((ROOT / "scripts" / "paraphrase_bank.json").read_text())["cases"]
        payloads = []
        for i in range(args.concurrency * args.rounds):
            p = dict(cases[i % len(cases)]["input"])
            p["operator_notes"] = [bank[(2 * i) % len(bank)]["note"], bank[(2 * i + 1) % len(bank)]["note"]]
            p["scenario_id"] = f"LOAD-{i:03d}"
            payloads.append(p)
    url = args.base_url.rstrip("/") + "/optimize-energy"
    results: list[tuple[float, int]] = []
    async with httpx.AsyncClient(timeout=40) as client:
        for rnd in range(args.rounds):
            batch = [one(client, url, payloads[(rnd * args.concurrency + i) % len(payloads)]) for i in range(args.concurrency)]
            results += await asyncio.gather(*batch)
    lat = sorted(t for t, _ in results)
    codes = [c for _, c in results]
    p95 = lat[max(0, int(round(0.95 * len(lat))) - 1)]
    print(f"requests={len(results)} ok={sum(1 for c in codes if c == 200)} non200={[c for c in codes if c != 200]}")
    print(f"p50={statistics.median(lat):.2f}s p95={p95:.2f}s max={lat[-1]:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
