# Cross-city planning model

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
| `estimated_recovery_minutes` | `max(10% of round-trip running time, 5 min)`. A standard planning approximation; no agency published this. |
| `estimated_cycle_time_minutes` | A **loop** is tested first: where the terminals are closer than both 1 km and 15% of the route's own length, the one-way circuit *is* the cycle and is not doubled -- several agencies publish a circular route as two directions, and adding them doubles the fleet. Otherwise both directions' runtimes plus recovery. Where neither applies - a one-way school tripper - **no cycle time is produced at all** rather than inventing a return leg. |
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

| City | Bus rows | Length km | Stops/km | Runtime min | Speed km/h | Headway min | Span h | Est. veh |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Calgary | 873 | 13.16 | 2.47 | 34.00 | 24.07 | 30.00 | 17.50 | 2.02 |
| Edmonton | 824 | 11.34 | 2.95 | 27.00 | 24.25 | 30.00 | 16.00 | 2.31 |
| Minneapolis | 504 | 19.24 | 2.72 | 40.00 | 27.52 | 30.00 | 16.00 | 3.00 |
| Montreal | 1017 | 10.88 | 3.72 | 38.00 | 17.11 | 30.00 | 18.90 | 2.96 |
| Portland | 339 | 18.00 | 2.75 | 48.00 | 23.04 | 30.00 | 16.85 | 4.22 |
| Seattle | 701 | 15.28 | 2.60 | 39.00 | 24.76 | 30.00 | 18.00 | 3.35 |
| Vancouver | 1248 | 11.44 | 2.79 | 30.00 | 22.45 | 29.35 | 17.52 | 2.96 |
| Winnipeg | 292 | 14.24 | 3.00 | 39.00 | 21.96 | 29.36 | 16.88 | 3.00 |

Dataset `cross_city_routes_v3`: 5,940 rows total, 5,798 bus, 8 cities,
0 duplicate identity keys.

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
least 50% of trips, speed in [5, 60] km/h, length in [1, 60] km, headway and span
present. 4,922 of 5,940 rows.

### Leave-one-city-out results, pooled

| Model | MAE km/h | RMSE | Median AE | P90 AE | Runtime MAE min |
| --- | --- | --- | --- | --- | --- |
| ridge | 3.359 | 4.271 | 2.798 | 6.908 | 5.20 |
| linear | 3.360 | 4.273 | 2.801 | 6.907 | 5.20 |
| random_forest | 3.457 | 4.425 | 2.842 | 7.251 | 5.37 |
| hist_gradient_boost | 3.470 | 4.437 | 2.769 | 7.395 | 5.42 |
| decision_tree | 3.821 | 4.879 | 3.129 | 8.013 | 5.99 |
| spacing_physical | 4.002 | 4.986 | 3.460 | 7.944 | 6.29 |
| global_median | 4.745 | 6.255 | 3.761 | 9.717 | 7.64 |

**Ridge wins.** Gradient boosting and random forests both lost: extra capacity
mostly buys extra opportunity to memorise the training cities. A deliberately
transparent two-parameter physical model - cruise speed and a per-stop time
penalty - is included as a floor, because a learned model that cannot beat the
relationship a planner would write down by hand has added nothing.

Weekday-only rerun: ridge 3.421 km/h on 1,884 rows - essentially unchanged,
which is the evidence that day-type near-duplicates are not inflating the
headline number.

### The physical model agrees with itself across every fold

| Held-out city | Fitted cruise km/h | Fitted stop penalty s |
| --- | --- | --- |
| Calgary | 40 | 24 |
| Edmonton | 40 | 25 |
| Minneapolis | 40 | 24 |
| Montreal | 36 | 18 |
| Portland | 39 | 23 |
| Seattle | 39 | 23 |
| Vancouver | 41 | 24 |
| Winnipeg | 40 | 24 |

Roughly 40 km/h cruise and a 24-second penalty per stop, in every fold, from data
alone. Montreal is the outlier at 36 km/h and 18 s - a denser, slower,
tighter-spaced system, which is exactly what it is.

### Per-city results

| Held-out city | Rows | Speed MAE km/h | Bias km/h | Change skill | Direction correct |
| --- | --- | --- | --- | --- | --- |
| Calgary | 659 | 2.986 | +0.743 | +11.2% | 71.4% |
| Edmonton | 660 | 3.570 | -2.215 | +8.7% | 70.0% |
| Minneapolis | 389 | 4.871 | -3.944 | +17.2% | 63.6% |
| Montreal | 883 | 4.162 | +3.677 | +36.8% | 82.4% |
| Portland | 272 | 2.510 | -0.065 | +12.8% | 66.9% |
| Seattle | 650 | 3.302 | -0.029 | +29.8% | 77.2% |
| Vancouver | 1130 | 2.935 | -0.034 | +20.7% | 71.3% |
| Winnipeg | 279 | 1.761 | +0.678 | -31.8% | 66.5% |

Two things a reader must not miss:

- **Per-city bias is large.** Held out, Montreal is over-predicted by +3.7 km/h
  and Minneapolis under-predicted by -3.9. Cities differ in ways the features
  cannot express. This is precisely why the product estimates a **difference**
  anchored to the route's measured speed rather than an absolute level.
- **Skill varies enormously by city, and Edmonton is weak.** Held out, Edmonton
  change-skill is only +8.7%, and Winnipeg is **negative** - its routes are so
  uniform that predicting no change beats the model. Edmonton is the city this
  product serves, so the UI must not quote the pooled +18.2% as if it applied
  to Edmonton.

### Feature reliance (held-out MAE increase when shuffled)

| Feature | km/h |
| --- | --- |
| mean_stop_spacing_m | +0.569 |
| stops_per_km | +0.393 |
| one_way_length_km | +0.210 |
| trips_per_day | +0.127 |
| median_stop_spacing_m | +0.108 |
| median_headway_minutes | +0.080 |
| peak_headway_minutes | +0.073 |
| service_span_hours | +0.061 |

Stop spacing and density dominate, and the fitted coefficient signs are
physically correct: denser stops slower, wider spacing faster, longer routes
faster. Nothing in the model behaves nonsensically.

### Deep model - attempted and rejected

A small residual MLP (width 64, 2 blocks, dropout, AdamW, Huber loss, early
stopping) was evaluated on the **identical** folds and cohort:
**3.986 km/h versus ridge 3.359** - it loses by 0.627 km/h, which is
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
| <= 0.5 | 1,010 | +0.5% | 58.2% |
| <= 1 | 4,547 | +2.3% | 60.3% |
| <= 1.5 | 10,206 | +6.2% | 65.0% |
| <= 2 | 15,205 | +8.2% | 67.8% |
| <= 3 | 29,655 | +13.3% | 71.9% |
| <= 4 | 114,840 | +10.2% | 68.1% |
| larger | 140,174 | +24.8% | 75.0% |

**For small edits the model is barely better than assuming the change does
nothing.** Skill only becomes substantial for large redesigns. The product shows
the figure for the bucket the current scenario falls into, not the pooled one,
and says so plainly when that figure is near zero.

Measuring the *free* operator instead — all features varying, which is not what
ships — would have reported +24.5% and 75.7% sign agreement.
Those are the numbers an earlier version of this document quoted; they describe
an estimator that is not served.

### Intervals

Measured from held-out within-city pairs, bucketed by how far apart the two
designs sit in standardized feature space:

| Design distance | Pairs | 80th pct | 90th pct |
| --- | --- | --- | --- |
| <= 0.5 | 1,010 | +/-4.15 | +/-6.31 |
| <= 1 | 4,547 | +/-5.38 | +/-7.39 |
| <= 1.5 | 10,206 | +/-5.73 | +/-7.81 |
| <= 2 | 15,205 | +/-5.96 | +/-8.04 |
| <= 3 | 29,655 | +/-6.26 | +/-8.48 |
| <= 4 | 114,840 | +/-6.23 | +/-8.40 |
| larger | 140,174 | +/-7.91 | +/-10.48 |

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
.\.venv\Scripts\python.exe scripts\fetch_gtfs_sources.py
.\.venv\Scripts\python.exe scripts\build_cross_city_dataset.py --version v3 --reference-date 2026-09-11
.\.venv\Scripts\python.exe scripts\evaluate_cross_city_baselines.py --dataset cross_city_routes_v3 --experiment cross_city_baselines_v3
.\.venv\Scripts\python.exe scripts\evaluate_scenario_deltas.py --dataset cross_city_routes_v3 --experiment cross_city_scenario_deltas_v3
.\.venv\Scripts\python.exe scripts\train_cross_city_deep_model.py --dataset cross_city_routes_v3 --experiment cross_city_deep_v3 --baselines cross_city_baselines_v3
.\.venv\Scripts\python.exe scripts\build_scenario_estimator.py --dataset cross_city_routes_v3 --version v3 --baselines cross_city_baselines_v3 --deltas cross_city_scenario_deltas_v3
```

The deep-model step is optional (it needs `pip install -e "apps/api[research]"`)
and does not affect the served estimator. Feeds downloaded on a later date
produce different numbers from the ones above.

Seed 20260912 throughout. Datasets, experiments and model artifacts all refuse to
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
