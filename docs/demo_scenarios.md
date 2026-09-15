# Demo scenarios

Two SCENARIOS examples for demos and screenshots. Both use a real Edmonton
Transit Service route, and both are modest, realistic changes. They were not
picked because the model gives dramatic numbers: example A is interesting
because the model says, correctly, that it **cannot** call a change this small.

Both start from **Route 002 (West Edmonton Mall – Stadium – Clareview),
direction 0, weekday**. This is the route SCENARIOS opens on by default
(`DEFAULT_SCENARIO_KEY = "002|0|weekday"` in `apps/web/app/scenarios/useScenarios.ts`).

Numbers below come from `POST /api/scenarios/estimate`, run against
`scenario_estimator_v3` / `cross_city_routes_v3` on 2026-09-12. The model is
frozen, so they reproduce exactly.

## Current published service (the baseline)

| Measure | Value | Source |
| --- | --- | --- |
| Route length | 16.81 km | measured from the GTFS shape |
| Stops | 58 (283 m mean spacing, 3.45 stops/km) | measured, dominant pattern (93% of trips) |
| Scheduled runtime | 51 min, one way | measured, median trip |
| Commercial speed | 19.8 km/h | measured, includes dwell |
| Peak headway | 10 min | measured at the busiest stop |
| Service span | 21.8 h | measured |
| Cycle time | 112 min | derived: round trip plus assumed 10% recovery |
| Vehicle requirement | 12 (ratio 11.22) | derived: cycle ÷ headway, rounded up |

Opening SCENARIOS with nothing changed shows exactly these values, labelled
**Published schedule** and **Confidence N/A**.

## A — Moderate route design change

Stop consolidation to roughly 350 m spacing plus a short terminal trim. Frequency
is unchanged.

| Control | From | To |
| --- | --- | --- |
| Route length | 16.8 km | **15.6 km** |
| Stops | 58 | **48** |
| Everything else | unchanged | unchanged |

API request body:

```json
{ "key": "002|0|weekday", "one_way_length_km": 15.6, "stop_count": 48 }
```

| Result | Current | Scenario (estimate) |
| --- | --- | --- |
| Runtime | 51 min | **45 min** (range 36–61) |
| Commercial speed | 19.8 km/h | **20.7 km/h** (range 15.4–26.1) |
| Cycle time | 112 min | 99 min (range 79–134) |
| Vehicles | 12 | **10** (range 8–14) |
| Confidence | — | **LOW** |

What to point out:

- The estimate is a **range**, and the range still contains today's runtime, so
  the change is shown in neutral colour rather than as a green improvement.
- The confidence reason is the measured number: for a change this size the
  model beats "assume no effect" by only about 2% on held-out cities and calls
  the direction right about 60% of the time.
- The fleet saving follows arithmetically from the shorter cycle, but it rests
  on the speed estimate and on an assumed recovery policy, and its range (8–14
  vehicles) still includes today's 12. It is an estimated requirement, rounded
  up to whole vehicles, not an ETS vehicle assignment.
- The map shows only the current alignment. The model changes length and stop
  count, not the path.

## B — Service (frequency) change

Geometry unchanged. The peak headway improves from 10 to 8 minutes.

| Control | From | To |
| --- | --- | --- |
| Peak headway | 10 min | **8 min** |
| Everything else | unchanged | unchanged |

API request body:

```json
{ "key": "002|0|weekday", "peak_headway_minutes": 8 }
```

| Result | Current | Scenario |
| --- | --- | --- |
| Runtime | 51 min | 51 min (held at schedule) |
| Commercial speed | 19.8 km/h | 19.8 km/h (as scheduled) |
| Cycle time | 112 min | 112 min |
| Vehicles | 12 | **15** (ratio 14.03) |
| Confidence | — | **N/A** — no model used for this change |

What to point out:

- Speed is **held on purpose**. Across cities, frequent routes tend to be slower,
  but that is because busy corridors get frequent service, not because frequency
  slows buses. Letting that correlation into the estimate would claim that
  better service makes the route slower. Frequency changes the fleet instead:
  cycle ÷ headway.
- The header basis reads **ARITHMETIC** (schedule plus recovery rule) and the
  panel says **Schedule arithmetic**: no model is used, and the only assumption
  is the recovery policy.
- No ridership or wait-time benefit is claimed. No adopted agency publishes
  route-level demand.

## Reproducing

With the API running:

```powershell
$body = '{ "key": "002|0|weekday", "one_way_length_km": 15.6, "stop_count": 48 }'
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/scenarios/estimate -ContentType application/json -Body $body
```

In the UI, open SCENARIOS (Route 002 loads by default) and move the **Route
length** and **Stops** sliders (A), or **Peak headway** only (B). **Reset all**
returns to the published schedule.
