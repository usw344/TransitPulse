"""Generate docs/cross_city_model.md from the artifacts, so it cannot drift."""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

qa = json.loads((ROOT / "artifacts/datasets/cross_city_routes_v3/qa_report.json").read_text(encoding="utf-8"))
base = json.loads((ROOT / "artifacts/experiments/cross_city_baselines_v3/report.json").read_text(encoding="utf-8"))
delt = json.loads((ROOT / "artifacts/experiments/cross_city_scenario_deltas_v3/report.json").read_text(encoding="utf-8"))
deep = json.loads((ROOT / "artifacts/experiments/cross_city_deep_v3/report.json").read_text(encoding="utf-8"))
meta = json.loads((ROOT / "artifacts/models/scenario_estimator_v3/metadata.json").read_text(encoding="utf-8"))
ridge_delta = next(r for r in delt["results"] if r["operator"].startswith("frozen"))
free_delta = next(r for r in delt["results"] if r["operator"].startswith("free"))
bucket_rows = "\n".join(
    "| %s | %s | %+.1f%% | %.1f%% |" % (
        ("<= %g" % b["max_feature_distance"]) if b["max_feature_distance"] < 1e8 else "larger",
        "{:,}".format(b["pairs"]),
        b["skill_vs_do_nothing"] * 100,
        b["sign_agreement_on_material_changes"] * 100,
    )
    for b in ridge_delta["by_design_distance"]
)
winner = base["winner"]
pc = base["primary"]["per_city"][winner]

fields = [
    "one_way_length_km", "stops_per_km", "scheduled_runtime_minutes",
    "scheduled_commercial_speed_kmh", "median_headway_minutes",
    "service_span_hours", "estimated_required_vehicles",
]
hdr = ["City", "Bus rows", "Length km", "Stops/km", "Runtime min", "Speed km/h", "Headway min", "Span h", "Est. veh"]

city_rows = []
for city, d in qa["distributions_bus_by_city"].items():
    cells = [city, str(qa["bus_rows_by_city"][city])]
    for f in fields:
        p = d.get(f)
        cells.append("%.2f" % p["median"] if p else "n/a")
    city_rows.append(cells)


def table(header, body):
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


rank = "\n".join(
    "| %s | %.3f | %.3f | %.3f | %.3f | %.2f |"
    % (r["model"], r["mae_kmh"], r["rmse_kmh"], r["median_ae_kmh"], r["p90_ae_kmh"], r["runtime_mae_minutes"])
    for r in base["ranking_by_mae_kmh"]
)
per_city = "\n".join(
    "| %s | %d | %.3f | %+.3f | %+.1f%% | %.1f%% |"
    % (c, v["n"], v["mae_kmh"], v["bias_kmh"],
       ridge_delta["per_city"][c]["skill_vs_do_nothing"] * 100,
       ridge_delta["per_city"][c]["sign_agreement_on_material_changes"] * 100)
    for c, v in sorted(pc.items())
)
imp = "\n".join(
    "| %s | %+.3f |" % (k, v)
    for k, v in sorted(base["permutation_importance_mae_increase_kmh"].items(), key=lambda i: -i[1])[:8]
)
phys_rows = "\n".join(
    "| %s | %.0f | %.0f |" % (c, p["cruise_kmh"], p["stop_penalty_seconds"])
    for c, p in base["primary"]["physical_parameters_by_fold"].items()
)
cal_rows = []
for b in meta["delta_error_buckets"]:
    edge = b["max_feature_distance"]
    label = ("<= %g" % edge) if edge < 1e8 else "larger"
    cal_rows.append("| %s | %s | +/-%.2f | +/-%.2f |" % (label, "{:,}".format(b["pairs"]), b["abs_error_p80_kmh"], b["abs_error_p90_kmh"]))
cal = "\n".join(cal_rows)

bus_total = sum(qa["bus_rows_by_city"].values())
wk = base["weekday_only"]["overall"]["ridge"]

doc = """# Cross-city planning model

What TransitPulse's SCENARIOS surface is built on, and what it is not.

This is an **experimental planning approximation**. It learns broad relationships
between service design and scheduled outcomes across eight real transit systems
and applies them to a proposed change. It is not a running-time study, it has
never seen a street or a signal, and it knows nothing about passengers.

## 1. Provenance of every field

The most important distinction in this dataset is between what a feed told us,
what follows arithmetically, and what was assumed. They are never mixed.

### MEASURED - read from official GTFS

| Field | How |
| --- | --- |
| `one_way_length_km` | Geodesic length of the dominant pattern's `shapes.txt` polyline. Falls back to the stop-to-stop path when a feed ships no usable shape, recorded as a distinct, weaker measurement in the row's provenance. |
| `stop_count` | Stops in the dominant stop pattern. |
| `mean_stop_spacing_m`, `median_stop_spacing_m` | Great-circle distance between consecutive stops of that pattern. |
| `scheduled_runtime_minutes` | Median over the pattern's trips of last arrival minus first departure. Never interpolated: a trip whose endpoints are untimed contributes nothing. |
| `peak_/offpeak_/median_headway_minutes` | Median gap between consecutive departures at the direction's **busiest stop**, over all of that direction's trips. |
| `service_span_hours`, `trips_per_day` | From the same departures. GTFS times past 24:00 are kept, so late-night service is not silently deleted. |
| `directness_ratio` | Straight-line terminal separation divided by path length. |
| `branch_count`, `dominant_pattern_trip_share` | Distinct stop patterns in the direction, and the share of trips running the dominant one. |

### DERIVED - arithmetic on measured values, no assumptions

| Field | Identity |
| --- | --- |
| `stops_per_km` | `stop_count / one_way_length_km` |
| `scheduled_commercial_speed_kmh` | `one_way_length_km / (scheduled_runtime_minutes / 60)`. Includes dwell; it is **not** a vehicle speed. |

### ESTIMATED - requires an operating assumption

| Field | Assumption |
| --- | --- |
| `estimated_recovery_minutes` | `max(10%% of round-trip running time, 5 min)`. A standard planning approximation; no agency published this. |
| `estimated_cycle_time_minutes` | A **loop** is tested first: where the terminals are closer than both 1 km and 15%% of the route's own length, the one-way circuit *is* the cycle and is not doubled -- several agencies publish a circular route as two directions, and adding them doubles the fleet. Otherwise both directions' runtimes plus recovery. Where neither applies - a one-way school tripper - **no cycle time is produced at all** rather than inventing a return leg. |
| `estimated_required_vehicles` | `cycle / headway`. An estimated **requirement**, never an assignment: real blocks interline across routes. |

### ABSENT by construction

Ridership, boardings, wait time, crowding, passenger benefit. No adopted agency
publishes comparable route-level demand, and vehicle positions are not demand.
`NormalizedRouteRecord` refuses a ridership value that does not name what it
counted, because boardings, unlinked trips, linked trips, APC counts and fare
validations are not interchangeable.

## 2. The eight cities

Bus rows only; medians. The differences here are **real structure**, not
extraction artefacts - Montreal's dense grid genuinely runs slower than
Minneapolis's long suburban routes, and learning that is the model's job.

%(city_table)s

Dataset `cross_city_routes_v3`: %(rows)s rows total, %(bus)s bus, %(ncities)d cities,
%(dupkeys)d duplicate identity keys.

## 3. Model

**Target:** `scheduled_commercial_speed_kmh`. Speed rather than runtime because
it is roughly scale-free and therefore transfers between cities; runtime is
recovered as `length / speed`, which keeps a scenario's arithmetic transparent.

**Leakage control:** commercial speed *is* length divided by runtime, so every
runtime-derived field (`scheduled_runtime_minutes`, cycle time, recovery, vehicle
requirement) is banned from the feature set, and a test asserts the ban.
`one_way_length_km` is kept: it is the numerator, but it is also a design
variable a planner chooses and is known before any runtime exists.

**Protocol:** leave-one-city-out. Every fold holds out one entire city. A random
row split would measure interpolation between neighbouring routes in a city the
model already knows, which is not the question the product asks.

**Cohort:** bus only, at least 8 trips/day, at least 8 stops, dominant pattern at
least 50%% of trips, speed in [5, 60] km/h, length in [1, 60] km, headway and span
present. %(cohort)s of %(dsrows)s rows.

### Leave-one-city-out results, pooled

| Model | MAE km/h | RMSE | Median AE | P90 AE | Runtime MAE min |
| --- | --- | --- | --- | --- | --- |
%(rank)s

**Ridge wins.** Gradient boosting and random forests both lost: extra capacity
mostly buys extra opportunity to memorise the training cities. A deliberately
transparent two-parameter physical model - cruise speed and a per-stop time
penalty - is included as a floor, because a learned model that cannot beat the
relationship a planner would write down by hand has added nothing.

Weekday-only rerun: ridge %(wkmae).3f km/h on %(wkn)s rows - essentially unchanged,
which is the evidence that day-type near-duplicates are not inflating the
headline number.

### The physical model agrees with itself across every fold

| Held-out city | Fitted cruise km/h | Fitted stop penalty s |
| --- | --- | --- |
%(phys)s

Roughly 40 km/h cruise and a 24-second penalty per stop, in every fold, from data
alone. Montreal is the outlier at 36 km/h and 18 s - a denser, slower,
tighter-spaced system, which is exactly what it is.

### Per-city results

| Held-out city | Rows | Speed MAE km/h | Bias km/h | Change skill | Direction correct |
| --- | --- | --- | --- | --- | --- |
%(percity)s

Two things a reader must not miss:

- **Per-city bias is large.** Held out, Montreal is over-predicted by %(mtl)+.1f km/h
  and Minneapolis under-predicted by %(mpls)+.1f. Cities differ in ways the features
  cannot express. This is precisely why the product estimates a **difference**
  anchored to the route's measured speed rather than an absolute level.
- **Skill varies enormously by city, and Edmonton is weak.** Held out, Edmonton
  change-skill is only %(edm)+.1f%%, and Winnipeg is **negative** - its routes are so
  uniform that predicting no change beats the model. Edmonton is the city this
  product serves, so the UI must not quote the pooled %(pooled)+.1f%% as if it applied
  to Edmonton.

### Feature reliance (held-out MAE increase when shuffled)

| Feature | km/h |
| --- | --- |
%(imp)s

Stop spacing and density dominate, and the fitted coefficient signs are
physically correct: denser stops slower, wider spacing faster, longer routes
faster. Nothing in the model behaves nonsensically.

### Deep model - attempted and rejected

A small residual MLP (width 64, 2 blocks, dropout, AdamW, Huber loss, early
stopping) was evaluated on the **identical** folds and cohort:
**%(deepmae).3f km/h versus ridge %(ridgemae).3f** - it loses by %(gap).3f km/h, which is
material. Its inner-validation MAE was around 2.0 while held-out was around 4.0:
it learned the training cities and did not transfer. Ridge ships; the network
stays as a recorded negative result.

## 4. How a scenario is answered

```
estimated_speed = measured_speed + (model(scenario) - model(current))
```

Anchoring to the route's own measured speed cancels the city and route offset,
and guarantees that a scenario changing nothing returns today's published
schedule exactly rather than an approximation of it.

**Frequency deliberately does not move estimated speed.** Fitted across the
dataset, higher frequency predicts *lower* speed - frequent routes run in dense,
congested corridors. That correlation is real but not causal, and an estimate
that improving a headway from 15 to 12 minutes would slow the route would not
be a reliable planning result. So headway, trips/day and span are held at the
current route's values on both sides of the difference; they cancel exactly, and
frequency instead does what it genuinely does - change the vehicle requirement,
through `cycle / headway`, which is arithmetic rather than inference.

### Skill by size of change — the number that actually matters

Pooled skill is dominated by pairs of wholly different routes. A planner trims a
few stops. Broken out by the same design distance the intervals use, measured
with the **served (frozen) operator**:

| Design distance | Pairs | Skill vs "no effect" | Direction correct |
| --- | --- | --- | --- |
%(buckets)s

**For small edits the model is barely better than assuming the change does
nothing.** Skill only becomes substantial for large redesigns. The product shows
the figure for the bucket the current scenario falls into, not the pooled one,
and says so plainly when that figure is near zero.

Measuring the *free* operator instead — all features varying, which is not what
ships — would have reported %(freeskill)+.1f%% and %(freesign).1f%% sign agreement.
Those are the numbers an earlier version of this document quoted; they describe
an estimator that is not served.

### Intervals

Measured from held-out within-city pairs, bucketed by how far apart the two
designs sit in standardized feature space:

| Design distance | Pairs | 80th pct | 90th pct |
| --- | --- | --- | --- |
%(cal)s

The band collapses to zero when nothing is changed and widens with the size of
the change. It is **conservative**: each measured pair carries two independent
route-identity offsets that an anchored estimate cancels, so these are upper
bounds. For a planning tool that is the right direction to err.

### Out-of-distribution guard

Every scenario input is checked against the 1st-99th percentile envelope of the
training data. Anything outside drops confidence to `low` and is named in the
response with its value and the trained range.

## 5. What this cannot do

- No ridership, wait time, crowding or passenger benefit. Nothing here is a
  passenger-optimal claim.
- It describes **scheduled** service design, not observed operations.
- It cannot evaluate rerouting a bus onto different streets. That needs a road
  network with corridor features and observed speeds - a future phase, not this
  model. The SCENARIOS map therefore draws the route as it runs today and says
  explicitly that no proposed alignment is shown.
- Held-out skill on within-city change is modest and varies by city, negative in
  the most uniform one.
- Eight agencies, all North American, all mid-size to large. Generalization
  beyond that is untested.

## 6. Reproducing

```powershell
.\\.venv\\Scripts\\python.exe scripts\\fetch_gtfs_sources.py
.\\.venv\\Scripts\\python.exe scripts\\build_cross_city_dataset.py --version v3 --reference-date 2026-09-11
.\\.venv\\Scripts\\python.exe scripts\\evaluate_cross_city_baselines.py --dataset cross_city_routes_v3 --experiment cross_city_baselines_v3
.\\.venv\\Scripts\\python.exe scripts\\evaluate_scenario_deltas.py --dataset cross_city_routes_v3 --experiment cross_city_scenario_deltas_v3
.\\.venv\\Scripts\\python.exe scripts\\train_cross_city_deep_model.py --dataset cross_city_routes_v3 --experiment cross_city_deep_v3 --baselines cross_city_baselines_v3
.\\.venv\\Scripts\\python.exe scripts\\build_scenario_estimator.py --dataset cross_city_routes_v3 --version v3 --baselines cross_city_baselines_v3 --deltas cross_city_scenario_deltas_v3
```

The deep-model step is optional (it needs `pip install -e "apps/api[research]"`)
and does not affect the served estimator. Feeds downloaded on a later date
produce different numbers from the ones above.

Seed %(seed)d throughout. Datasets, experiments and model artifacts all refuse to
overwrite an existing version.

### Why the dataset is at v3

**v1 -> v2**: a loop published as two directions was having its two circuits
added together, doubling its cycle time and fleet (108 rows); the loop test was
also calling every route under ~2 km a loop regardless of shape; and rows from
multi-operator feeds carried the feed's first agency rather than their own
route's, mislabelling 127 Edmonton-region rows, 32 Sound Transit rows and 12
Portland Streetcar rows.

**v2 -> v3**: the extractor was **not deterministic**. Every stop on an
unbranched route is served by every trip, so the "busiest stop" is a tie among
all of them, and `Counter.most_common` broke that tie by insertion order --
which followed set iteration over stop id *strings*, and Python randomizes
string hashing per process. 280 of 842 Edmonton rows changed between two builds
of identical code on an identical feed. Ties are now broken on the value itself,
and a test pins it. Two runs under different `PYTHONHASHSEED` values now produce
byte-identical output.

## 7. Milestone N — the experimental optimizer

`transitpulse_ml/service_optimizer.py` searches service design, and is
deliberately narrow. It optimizes **only** headways, span and the recovery
policy -- variables whose consequences for fleet and vehicle-hours are
arithmetic -- and holds runtime at the route's measured scheduled value without
consulting the speed model at all.

Geometry is excluded, and every result says why: with held-out skill on small
design changes near zero, a search over stop count or route length would
optimize model error and return a confident answer built from noise.

The objective is explicit and persisted with every result. Its default weights
value waiting **above** vehicles, because the first version did not: with
waiting cheap relative to buses, the arithmetic optimum was the emptiest
timetable the constraints allowed. The defaults now hold span and fleet at the
route's current values, so a better design has to serve the same day with the
same buses rather than score well by running less.

Known limitation, stated in every result: the search will always choose the
minimum allowed recovery, because a shorter layover is arithmetically a shorter
cycle. Nothing models what recovery is *for*.
""" % {
    "city_table": table(hdr, city_rows),
    "rows": "{:,}".format(qa["row_count"]),
    "bus": "{:,}".format(bus_total),
    "ncities": len(qa["cities"]),
    "dupkeys": qa["duplicate_identity_keys"],
    "cohort": "{:,}".format(base["cohort_rows"]),
    "dsrows": "{:,}".format(base["dataset_rows"]),
    "rank": rank,
    "wkmae": wk["mae_kmh"],
    "wkn": "{:,}".format(wk["n"]),
    "phys": phys_rows,
    "percity": per_city,
    "mtl": pc["Montreal"]["bias_kmh"],
    "mpls": pc["Minneapolis"]["bias_kmh"],
    "edm": ridge_delta["per_city"]["Edmonton"]["skill_vs_do_nothing"] * 100,
    "pooled": ridge_delta["pooled"]["skill_vs_do_nothing"] * 100,
    "imp": imp,
    "deepmae": deep["overall"]["mae_kmh"],
    "ridgemae": base["ranking_by_mae_kmh"][0]["mae_kmh"],
    "gap": deep["comparison_to_baselines"]["deep_minus_baseline_kmh"],
    "cal": cal,
    "buckets": bucket_rows,
    "freeskill": free_delta["pooled"]["skill_vs_do_nothing"] * 100,
    "freesign": free_delta["pooled"]["sign_agreement_on_material_changes"] * 100,
    "seed": base["seed"],
}

out = ROOT / "docs" / "cross_city_model.md"
out.write_text(doc, encoding="utf-8")
print("wrote", out, len(doc.splitlines()), "lines")
