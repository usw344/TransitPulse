"""Milestone N — run the experimental service-design search for one route.

Run:

    .venv\\Scripts\\python.exe scripts\\run_service_optimizer.py --key "002|0|weekday"

This is a **foundation**, not a product surface. It is exposed as a script and
not wired into the UI on purpose: the estimator's measured skill on small design
changes is close to zero, and putting a screen in front of a planner that says
"here is the best design" would imply a confidence this project has not earned.

What the search does have is a defensible footing, because it deliberately
refuses to search the part the model is bad at. It optimizes only headways, span
and the recovery policy — whose consequences for fleet and vehicle-hours are
arithmetic — and holds runtime at the route's measured scheduled value without
consulting the speed model at all. See ``transitpulse_ml/service_optimizer.py``.

Results are written to ``artifacts/experiments/service_optimizer_<version>/``
with the objective weights and constraints attached, because changing either
changes the answer and an answer without them cannot be read.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))

from transitpulse_api.scenarios import ScenarioUnavailable, load_scenario_service  # noqa: E402
from transitpulse_ml.service_optimizer import (  # noqa: E402
    ServiceConstraints,
    ServiceObjective,
    optimize_service,
)

EXPERIMENT_ROOT = REPO_ROOT / "artifacts" / "experiments"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="002|0|weekday", help="scenario route key")
    parser.add_argument("--version", default="v1")
    parser.add_argument(
        "--max-vehicles",
        type=float,
        default=None,
        help="fleet ceiling; defaults to the route's current requirement, i.e. "
        "'do better without asking for more buses'",
    )
    parser.add_argument(
        "--min-span-hours",
        type=float,
        default=None,
        help="span floor; defaults to the route's CURRENT span, so the search cannot "
        "answer by shortening the service day",
    )
    parser.add_argument("--max-peak-headway", type=float, default=30.0)
    args = parser.parse_args()

    try:
        service = load_scenario_service()
    except ScenarioUnavailable as error:
        print(f"scenario artifacts unavailable: {error}", file=sys.stderr)
        return 2

    try:
        baseline = service.baseline(args.key)
    except KeyError:
        print(f"unknown route key {args.key!r}", file=sys.stderr)
        return 2

    runtime = baseline.row.get("scheduled_runtime_minutes")
    if not runtime:
        print("this route direction has no scheduled runtime to hold", file=sys.stderr)
        return 2

    current_vehicles = baseline.row.get("estimated_required_vehicles")
    ceiling = args.max_vehicles or (current_vehicles or 12.0)

    # Default the floors to what the route already does, so a "better" design
    # has to be better at serving the same day with the same fleet -- not
    # cheaper because it runs less.
    current_span = baseline.row.get("service_span_hours") or 14.0
    constraints = ServiceConstraints(
        max_vehicles=ceiling,
        min_service_span_hours=args.min_span_hours or min(current_span, 24.0),
        max_peak_headway_minutes=args.max_peak_headway,
    )
    result = optimize_service(
        current=baseline.to_plan(),
        runtime_minutes=runtime,
        opposite_runtime_minutes=service.opposite_runtime_minutes(baseline),
        constraints=constraints,
        objective=ServiceObjective(),
    )

    out_dir = EXPERIMENT_ROOT / f"service_optimizer_{args.version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.key.replace('|', '_')}.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "command": f"python scripts/run_service_optimizer.py --key \"{args.key}\" "
        f"--version {args.version}",
        "route_key": args.key,
        "route_label": baseline.route_label,
        "agency": baseline.row.get("agency"),
        "dataset": service.dataset,
        **result,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    current = result["current"]
    print(f"Route {baseline.route_label} dir {baseline.direction_id} {baseline.day_type} "
          f"— {baseline.row.get('agency')}")
    print(f"  runtime held at {runtime:.0f} min (measured); speed model not consulted")
    print(f"  fleet ceiling   {ceiling:.2f} vehicles")
    print(f"\n  CURRENT  peak {current['peak_headway_minutes']} min · "
          f"off-peak {current['offpeak_headway_minutes']} min · "
          f"span {current['service_span_hours']} h · "
          f"cycle {current['cycle_time_minutes']} min · "
          f"{current['peak_vehicles']} vehicles")
    print(f"\n  {result['feasible_count']:,} feasible designs of "
          f"{result['combinations_evaluated']:,} evaluated\n")
    print(f"  {'peak':>5s} {'offpk':>6s} {'span':>6s} {'recov':>6s} {'cycle':>7s} "
          f"{'veh':>6s} {'veh-h':>7s} {'score':>7s}")
    for candidate in result["best"][:6]:
        print(f"  {candidate['peak_headway_minutes']:5.0f} "
              f"{candidate['offpeak_headway_minutes']:6.0f} "
              f"{candidate['service_span_hours']:6.1f} "
              f"{candidate['recovery_fraction']:6.0%} "
              f"{candidate['cycle_time_minutes']:7.1f} "
              f"{candidate['peak_vehicles']:6.2f} "
              f"{candidate['vehicle_hours']:7.1f} "
              f"{candidate['score']:7.3f}")
    print(f"\n  geometry excluded: {result['geometry_excluded']['reason'][:96]}...")
    print(f"\n-> {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
