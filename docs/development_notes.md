# Development notes

Operating details, rebuild procedures and a short project history for anyone
running or changing TransitPulse. The [README](../README.md) covers setup and
tests.

## Operating notes

- **Restart the API after backend changes.** `START_TRANSITPULSE.bat` reuses
  anything already listening on port 8000 and prints a warning when it does.
  After changing API code, stop the running `uvicorn transitpulse_api.main`
  process first, relaunch, and confirm the new process owns the port:

  ```powershell
  Get-NetTCPConnection -LocalPort 8000 -State Listen
  Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId,CreationDate,CommandLine
  ```

  An HTTP 200 from an old process proves nothing about new code. During
  development an orphaned `--reload` worker kept serving stale code while every
  new server failed to bind the port.
- **Run one recorder.** The launcher does not start a second
  `transitpulse_api.realtime_recorder` if one is running. Two recorders would
  double the load on the publisher and race for the same observation keys.
  Starting two launchers at the same instant could still start two; the unique
  observation key prevents duplicate rows, so the effect is log noise or a
  missed poll, not corrupt data.
- **pytest temp directory on Windows.** pytest's default temp directory can be
  unwritable, which turns every `tmp_path` test into a setup error. Always pass
  `--basetemp` (the README commands and `scripts/setup_test_database.py
  --pytest` do).
- **`alembic check`** always reports PostGIS's own `spatial_ref_sys` table. That
  table belongs to the extension; it is not schema drift.
- **Line endings.** `START_TRANSITPULSE.bat` must keep CRLF line endings, which
  `.gitattributes` enforces. `cmd.exe` can mis-resolve `goto` labels in LF-only
  batch files.
- **Partially built model folders.** If a model build is interrupted, the
  half-written `artifacts/models/<version>` folder makes SCENARIOS return HTTP
  500 instead of the clean 503 for a missing model. Delete that folder and
  rebuild.

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| "The TransitPulse API is not responding" | The API process is not running. Run `START_TRANSITPULSE.bat` again; it reuses anything already running. |
| "No basemap — the tile server could not be reached" | No internet access to the CARTO tile host. Transit data still draws from the local database. |
| LIVE shows STALE | The recorder is stopped or the ETS feed paused. REPLAY, ANALYTICS and SCENARIOS do not depend on live data. |
| SCENARIOS says the estimator is unavailable | The `artifacts/` model files are missing (for example on a fresh clone). Build them with the commands in the README. |
| ANALYTICS refuses a range | The window exceeds 24 hours or contains more than 10,000 observations. Choose a shorter window. |

## MapLibre worker and Turbopack

MapLibre 6 loads its tile-parsing worker as an ES module located with
`import.meta.url`. After Turbopack bundling, that URL points into
`/_next/static/chunks/`, where the file does not exist. Next answers with its
HTML 404 page, the browser refuses the module because of its MIME type, and
vector tiles silently never parse (raster tiles still render, which made the
fault hard to see). `apps/web/scripts/copy-maplibre-worker.mjs` runs on
`predev` and `prebuild`, copies the worker and its shared chunk into
`public/maplibre/`, and `page.tsx` calls `setWorkerUrl()`.

Two measurement traps when checking the map: tile fetches happen inside the
worker, so they never appear in main-thread `performance` entries; and
repeatedly reloading a page in an automated browser can exhaust WebGL
contexts. `scripts/portfolio_capture/map_qa.py` checks the map in a fresh
headless Chrome or Edge profile (set `TRANSITPULSE_CHROME` if the browser is
not found).

## Frozen dataset and model

| Item | Value |
| --- | --- |
| Dataset | `artifacts/datasets/cross_city_routes_v3`: 5,940 rows, 8 cities, schema 1.2.0; bus modelling cohort 4,922 rows / 1,919 route directions |
| Dataset checksum | `rows.jsonl` SHA-256 `475511205936FA7BBD63…` (matches the manifest and the model metadata) |
| Model | `artifacts/models/scenario_estimator_v3`: ridge regression, seed 20260912; `model.pkl` SHA-256 `74B7370530A6445A257F…` |
| Held-out accuracy | leave-one-city-out MAE 3.36 km/h |

These artifacts are generated locally and are not in version control. The
API loads `DEFAULT_DATASET` and `DEFAULT_MODEL` from
`apps/api/transitpulse_api/scenarios.py`.

## Rebuilding a new version

Every script refuses to overwrite an existing version, so a rebuild uses a new
version name for every stage:

```powershell
$V = "v4"
.\.venv\Scripts\python.exe scripts\fetch_gtfs_sources.py
.\.venv\Scripts\python.exe scripts\build_cross_city_dataset.py --version $V --reference-date <YYYY-MM-DD>
.\.venv\Scripts\python.exe scripts\evaluate_cross_city_baselines.py --dataset cross_city_routes_$V --experiment cross_city_baselines_$V
.\.venv\Scripts\python.exe scripts\evaluate_scenario_deltas.py --dataset cross_city_routes_$V --experiment cross_city_scenario_deltas_$V
.\.venv\Scripts\python.exe scripts\train_cross_city_deep_model.py --dataset cross_city_routes_$V --experiment cross_city_deep_$V --baselines cross_city_baselines_$V
.\.venv\Scripts\python.exe scripts\build_scenario_estimator.py --dataset cross_city_routes_$V --version $V --baselines cross_city_baselines_$V --deltas cross_city_scenario_deltas_$V
```

Then update `DEFAULT_DATASET` and `DEFAULT_MODEL` in
`apps/api/transitpulse_api/scenarios.py` and the artifact paths in
`scripts/generate_model_doc.py`, and regenerate `docs/cross_city_model.md`.

## Research experiments (not product features)

Before SCENARIOS, the project tried to build a travel-time model and a
route simulation prototype for a single Edmonton route (Route 004) from recorded
vehicle positions. The results are visible at `/research/model-lab` when the
local research artifacts exist.

- The labelled dataset covered 1,989 stop-to-stop segment traversals over four
  sparse service days (2026-09-07 to 2026-09-10).
- On the held-out day (326 segments), a train-only segment median baseline had
  a mean absolute error of 20.2 s. A small neural network scored 28.9 s and was
  rejected.
- Simulator validation required at least 30 complete terminal-to-terminal runs
  across at least three service dates, with thresholds fixed in advance. None
  of the 68 recorded runs covered every stop-to-stop edge of the route, so that
  validation was never reached and no optimization was run against the
  simulator.

The recorded history was too sparse and discontinuous for that approach, which
is why the planning question moved to scheduled service across eight cities.

## Project history

| Date (2026) | Milestone |
| --- | --- |
| Sep 7 | FastAPI, Next.js and PostGIS foundation with Alembic. Feed-versioned static GTFS import, network API and MapLibre map. GTFS-Realtime ingestion, current state, operations thresholds, append-only vehicle observation history and the Windows launcher. |
| Sep 8 | Map fallback for machines where WebGL fails. An early integration-test run truncated the local application database; the feed was re-imported and test fixtures now refuse any database not named as a test database. Bounded historical replay. |
| Sep 9 | LIVE, REPLAY and ANALYTICS modes. Route reliability analytics, a network health summary and two-period comparison with a coverage guard. |
| Sep 10 | Read-only audit of recorded history (sparse, with two long outages), external data register, segment geometry primitives and GTFS service-day resolution. |
| Sep 11 | Correctness fixes to headways (prediction horizon, feed jitter, per-direction grouping). Trip-update observation history and database immutability triggers (migrations 0006, 0007). Single-route research experiments. Cross-city data source register started. |
| Sep 12 | MapLibre worker fix. Eight-agency GTFS normalizer and datasets v1 to v3. Baselines, rejected neural network and the scenario estimator. SCENARIOS UI. Experimental service optimizer (not in the UI). Final defect fixes, an isolated test database script and portfolio screenshots. |

Defects fixed near the end, for reference: the route operations panel returned
HTTP 500 for early-running routes (a missing status key); numpy and
scikit-learn were imported but not declared as dependencies; the fleet interval
scaled the return trip incorrectly, so the vehicle range was too narrow; route
status used the single worst trip, which labelled 39 of 103 routes MAJOR DELAY
next to average delays of about 2 minutes; and the default ANALYTICS window
exceeded the API's observation cap on busy routes.
