"""POST every public sample case to a running service and replay the result like the judge.

Usage:  uv run python scripts/run_public_samples.py --base-url http://localhost:8000
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.directives import build_constraints  # noqa: E402
from app.schemas import DirectiveInterpretation, HourlyPlanEntry, OptimizeRequest  # noqa: E402
from app.validator import validate_plan  # noqa: E402


def same_adjustment(got, want) -> bool:
    if got is None or want is None:
        return got is want
    if got.get("hours") != want.get("hours"):
        return False
    return all(abs(float(got.get(k, 1e9)) - float(want[k])) <= 0.01 for k in ("factor", "minimum_energy_kwh", "max_grid_kwh") if k in want)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--cases", default=str(ROOT / "samples" / "public_cases.json"))
    ap.add_argument("--only", help="comma-separated case ids")
    args = ap.parse_args()

    cases = json.loads(Path(args.cases).read_text())["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    base = args.base_url.rstrip("/")
    with httpx.Client(timeout=40) as client:
        h = client.get(f"{base}/health")
        print(f"GET /health -> {h.status_code} {h.text.strip()}")
        failures = 0
        for case in cases:
            t0 = time.perf_counter()
            r = client.post(f"{base}/optimize-energy", json=case["input"])
            dt = time.perf_counter() - t0
            if r.status_code != 200:
                print(f"[{case['id']}] HTTP {r.status_code} in {dt:.2f}s: {r.text[:200]}")
                failures += 1
                continue
            body = r.json()
            req = OptimizeRequest.model_validate(case["input"])
            exp = case["expected_output"]

            interp_ok = True
            for got, want in zip(body["directive_interpretation"], exp["directive_interpretation"]):
                if got["directive_type"] != want["directive_type"] or got["applies"] != want["applies"] \
                        or not same_adjustment(got["structured_adjustment"], want["structured_adjustment"]):
                    interp_ok = False
                    print(f"    note {want['note_index']}: got {got['directive_type']} {got['structured_adjustment']} "
                          f"want {want['directive_type']} {want['structured_adjustment']}")
            if len(body["directive_interpretation"]) != len(exp["directive_interpretation"]):
                interp_ok = False

            # Replay against the ORGANIZER ground-truth directives, exactly like the judge.
            truth = [DirectiveInterpretation.model_validate(d) for d in exp["directive_interpretation"]]
            cons = build_constraints(truth, req.battery)
            plan = [HourlyPlanEntry.model_validate(p) for p in body["hourly_plan"]]
            errors = validate_plan(req.sorted_hours(), req.battery, cons, plan,
                                   (body["total_grid_kwh"], body["total_cost_bdt"], body["peak_grid_kwh"]))
            ratio = min(1.0, exp["total_cost_bdt"] / body["total_cost_bdt"]) if body["total_cost_bdt"] > 0 else 1.0
            status = "OK " if interp_ok and not errors else "BAD"
            if status == "BAD":
                failures += 1
            print(f"[{case['id']}] {status} {dt:.2f}s interp={'ok' if interp_ok else 'MISMATCH'} "
                  f"valid={'yes' if not errors else 'NO'} cost={body['total_cost_bdt']} ref={exp['total_cost_bdt']} quality={ratio:.4f}")
            for e in errors[:5]:
                print(f"    {e}")
    print(f"\n{len(cases) - failures}/{len(cases)} cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
