# TransitPulse worklog

<!-- Append compact, factual handoff entries below. -->

## 2026-09-07 — M0 foundation

- Added Docker Compose `db` (PostGIS), `api` (FastAPI/SQLAlchemy 2), `web` (Next/React/TS/Tailwind); web rewrites `/api/*` to API, API has `/health` and real-query `/health/db`.
- Alembic is root-owned; `0001_enable_postgis` creates only `postgis` extension. No transit domain schema/data exists. DB config is `TRANSITPULSE_DATABASE_URL`.
- Added real-DB/PostGIS pytest coverage and CI PostGIS service + migration + frontend typecheck/build. Docker API image includes test dependencies for `docker compose exec api pytest`.
- Verified: `npm ci`, `npm run typecheck`, `npm run build` (Next 16.3.3); API health pytest (1 passed); `alembic upgrade head --sql` emits extension SQL; `compileall`; `git diff --check`.
- Known runtime gap: this host has no `docker`/Podman/local PostgreSQL and port 5432 is closed; Compose launch and the two real PostGIS tests were not executable here. Do not treat them as host-verified.
- Next: on a Docker-capable host run `docker compose up --build`, inspect web status, then `docker compose exec api pytest`; after M0 runtime verification, begin M1 GTFS static-feed domain/import design.

## 2026-09-07 — M0 native recheck

- Native Python 3.14.2 and Node 24.12.0 are available. No PostgreSQL client/service is installed and localhost:5432 is closed, so native `alembic upgrade head`, real `/health/db`, and integration tests remain unverified here; no Docker was used.
- DB connection timeout is bounded to 3 seconds; integration tests skip only when the configured PostGIS database or migrated schema is unavailable. API-only health test runs locally.

## 2026-09-07 — M1 static GTFS

- Added migration `0002_gtfs_static_network` and `models.py`: immutable `gtfs_feeds` provenance keyed by SHA-256; all agencies/routes/stops/calendars/calendar dates/shapes/trips/stop times are feed-scoped. Stops are `POINT(4326)` and grouped shapes are `LINESTRING(4326)` with GIST indexes.
- `transitpulse_api.gtfs` validates a ZIP before writes, then imports all rows in one transaction. Same checksum returns the existing version; failure rolls back all domain rows. Trips and stop times are batch-inserted for the real multi-million-row feed. CLI: `.\.venv\Scripts\python.exe -m transitpulse_api.import_gtfs [--file path|--url url]`.
- Default configurable source is the official City of Edmonton ETS URL `https://gtfs.edmonton.ca/TMGTFSRealTimeWebService/GTFS/GTFS.zip`; importer uses an explicit User-Agent because the provider rejects Python's default request.
- Read-only source verification on 2026-09-07: 16,828,874 bytes; service dates 2026-09-02 through 2026-11-28; 7 agencies, 242 routes, 6,791 stops, 86,754 trips, 2,786,938 stop times, 657 grouped shapes from 244,529 shape points, 1,336 service calendars. Not DB-imported because native PostGIS is absent.

## 2026-09-07 — M2/M3

- Added typed FastAPI feed-scoped endpoints: `/api/agencies`, `/api/routes`, `/api/routes/{route_id}`, `/api/routes/{route_id}/stops`, `/api/routes/{route_id}/shape`, `/api/stops/{stop_id}`, and one-time `/api/network/shapes` for MapLibre.
- Rebuilt `apps/web/app/page.tsx` as an Edmonton MapLibre network viewer with route search/type filters, route selection/detail, selected shape/stops, stop popups, and API/PostGIS status. It loads background route geometry once and only selected-route data after that.
- Verified: `alembic upgrade head --sql`; API compile; `pytest` = 3 passed / 7 skipped (missing local PostGIS); `npm run typecheck`; `npm run build`. Next: install/start native PostgreSQL + PostGIS, apply migration, import the official feed, run the skipped API/integration tests, then inspect the populated map in browser.

## 2026-09-07 — final runtime correction

- Next now preserves `/api` in its proxy destination. FastAPI exposes both `/health` + `/health/db` (M0 compatibility) and proxy aliases `/api/health` + `/api/health/db`; database-unavailable network/feed resolution returns typed HTTP 503 instead of 500.
- Native launch check: FastAPI `/health` and `/api/health` returned 200. Through the running Next proxy, `/api/routes` reached FastAPI and returned the expected 503 while PostGIS is absent. Both temporary dev servers were stopped after verification.

## 2026-09-07 — M4/M5/M6 implementation checkpoint

- M4 code: official ETS GTFS-Realtime URLs are configurable in `config.py` / `.env.example`; `realtime.py` fetches/parses protobuf, safely normalizes optional fields, matches current static IDs, preserves feed timestamps, retains old state on source failure, and exposes `/api/realtime/status`, vehicles, route vehicles, and alerts. `page.tsx` uses a MapLibre GeoJSON source/layers with 15-second polling (no WebSockets).
- Official source check (actual): `VehiclePositions.pb` yielded 267 entities/vehicles, `TripUpdates.pb` 2,094 entities/1,254 normalized updates, and `Alerts.pb` 85 entities/alerts; all source timestamps were `2026-09-07T21:26:00Z`.
- M5 code: `operations.py` centralizes configurable on-time (60s), major-delay (300s), bunching (0.5x), and gap (1.75x) thresholds. Route endpoint `/api/operations/routes/{route_id}` reports only active vehicles, delay, route alerts, and comparable predicted-arrival headways; selected-route UI displays its status/reason/metrics.
- M6 code: migration `0004_vehicle_observation_history` records deduplicated current vehicle observations with immutable `static_feed_id`, spatial/time indexes, and bounded (max 24h, max 5,000) `/api/history/vehicles/{vehicle_id}` + `/api/history/routes/{route_id}`. `realtime_recorder.py` is the single-process polling worker.
- `START_TRANSITPULSE.bat` now checks Python dependencies, PostgreSQL/PostGIS, applies migrations, imports only if needed, starts API/web/recorder, waits for web, and opens the browser. Its failure-path invocation was verified.
- Runtime blocker remains: no system PostgreSQL/PostGIS is installed. A temporary portable PostgreSQL 18.6 cluster was initialized and reached at `localhost:5432`, but the official PostGIS 3.6.2 installer will not install into an unregistered portable server. Real `alembic upgrade head` stops at `CREATE EXTENSION postgis`; therefore M0-M3 DB integration, Edmonton import/map, M4 persistence, M5 DB API, M6 recording, and full launcher startup are still unverified.
- Verified this run: `compileall`; 11 unit/API-independent tests passed (9 PostGIS integration tests skipped); frontend typecheck + production build; generated Alembic SQL through `0004`; live official protobuf fetch/parse; launcher expected PostGIS failure. Next: install a matching PostGIS bundle in a native PostgreSQL installation, run `START_TRANSITPULSE.bat`, then execute the skipped integration tests and browser map check.

## 2026-09-07 — worklog audit correction

- Read the full log against current code. Earlier statements that port 5432 was closed and no native PostgreSQL client existed were true at their timestamps; this run temporarily started a project-local PostgreSQL 18.6 server to prove connectivity, then stopped it. The current blocker is specifically missing PostGIS extension files, not PostgreSQL reachability. No migration/endpoint mismatch found; the historical test counts remain historical, while current verification is 11 passed / 9 skipped.

## 2026-09-07 — native PostGIS import and launcher correction

- Fixed GTFS `stops.txt` validation to follow `location_type`: `stop_name`, `stop_lat`, and `stop_lon` remain required for types 0/1/2 and are optional for generic nodes/boarding areas (3/4); parent hierarchy and stop-time platform references are now also validated. Edmonton row 6258 (`E10`, type 3, blank name) and 40 equivalent valid nodes import unchanged. Deterministic tests cover permitted/rejected blank names, the Edmonton row, conditional parents/coordinates, and existing malformed inputs.
- Fixed `START_TRANSITPULSE.bat` bootstrap order: it separately checks PostgreSQL reachability and server-side PostGIS files, runs Alembic (where 0001 enables PostGIS), then verifies the extension is active. The readiness HTTP probes bypass Windows PowerShell's localhost proxy behavior and failure paths identify PostgreSQL, extension files, migration, GTFS, backend, and frontend failures. A temporary clean database proved PostGIS is inactive before migration and active after it; the test database was removed without touching the populated TransitPulse database.
- Real official Edmonton import succeeded: 7 agencies, 242 routes, 6,791 stops, 86,754 trips, 2,786,938 stop times, and 657 shapes. All 27 backend tests (including PostGIS integration) passed in the isolated database; frontend typecheck and production build passed. `START_TRANSITPULSE.bat` completed, rendered 242 routes and route 001A's 54 stops, and the realtime recorder successfully ingested 146 vehicles, 835 trip updates, and 85 alerts and recorded 146 observations. Remaining issues: none known.

## 2026-09-08 — Stage 0 visual/runtime correction

- Diagnosed this host's blank MapLibre canvas as a graphics-process failure, not missing GTFS data. The normal map keeps the Carto/OpenStreetMap MapLibre basemap; an SVG fallback renders the same API-provided 657 static GTFS shapes, selected route geometry/stops, and live vehicle GeoJSON so the map remains useful without working WebGL. Sidebar panels are bounded and independently scrollable, eliminating footer/detail overlap.
- An earlier local integration run exposed that fixtures truncated the application database. The official Edmonton feed was restored via `START_TRANSITPULSE.bat`; fixtures now require `TRANSITPULSE_TEST_DATABASE_URL` unless an explicit disposable-CI opt-in is set. Dedicated `transitpulse_test` migrations plus the full suite passed (27), while the default safe run reports 17 passed / 10 skipped.
- Runtime recheck: 242 routes, 657 shapes, 376 live vehicles, and route 001A's 3 shapes / 54 stops. Independent Stage 0 supervisor verdict: PASS.

## 2026-09-08 — M7 historical replay

- Added feed-consistent history availability and bounded observation endpoints. Replay requests are restricted to the current static feed, a maximum 24-hour window, and 5,000 source observations; route and vehicle filters are supported without permitting cross-feed results.
- Added Live/Replay controls with date-time bounds, timeline scrubbing, ±30-second jumps, play/pause, and 1×/5×/20×/60× speeds. The map renders only the latest source record at or before the playhead, hides vehicles after a two-minute source gap, and never interpolates or invents locations. Current live operations are hidden in replay mode.
- Runtime proof: route 004 loaded 133 bounded recorded observations from the current feed, selected-route geometry/stops remained visible, backward scrubbing and 60× playback worked, and Live restored current operations. Full backend suite: 28 passed; replay helpers: 5 passed; typecheck and production build passed. Independent M7 supervisor verdict: PASS.

## 2026-09-09 — Operations product pass, Stage 1 and M8

- Reworked the web product into explicit LIVE / REPLAY / ANALYTICS modes with a network KPI strip, balanced map/workspace layout, searchable status-aware route list, route inspector, ranked issue and publisher-alert surfaces, and preserved M7 playback. Browser checks at 1920×1080 and 1440×900 found no document overflow or overlapping controls. Independent Stage 1 supervisor verdict: PASS.
- Added bounded, feed-scoped route reliability analytics: observed vehicles, delay percentiles/bands, scheduled vs observed headways, variability, bunching/gaps, time buckets, best/worst periods, stop-level reliability evidence, coverage, and explicit configured sufficiency gates. Unsupported low-sample estimates are withheld in both analytics surfaces.
- Deterministic coverage includes calculations, sparse/empty windows, 24-hour/10,000-row bounds, and overlapping route/trip identifiers across static feeds. Independent M8 review reproduced route 705 UI/API values from raw SQL and returned PASS.

## 2026-09-09 — M9 network health and historical comparison

- Added one network-health endpoint for active vehicles/routes, late routes, bunching, gaps, alerts, median delay, and a bounded ranked issue queue. Early running is distinct from late delay; stale snapshots withhold current KPIs/issues instead of ranking old state. Route/alert selection focuses route geometry and opens a live-refreshed evidence summary.
- GTFS-Realtime alerts expose publisher effect, affected routes, active periods, and route navigation without invented severity. Route-list status lookup is map-backed rather than repeated linear scans.
- Added bounded two-period route comparison with median/P90 delay, observed/scheduled headway evidence, variability, bunching, gaps, observations, coverage, B−A deltas, an explainable verdict, and a 75% recorded/requested coverage guard that refuses a winner when comparison quality is inadequate.
- Current automated verification: 40 dedicated PostGIS backend tests, 3 analytics UI-gate tests, 5 replay tests, frontend typecheck, and production build passed with no skipped tests in the dedicated suite. Real Edmonton route 705 comparison used two six-hour windows (1,300 vs 1,167 observations; 82.4% coverage match); live network refresh exposed 559 vehicles, 150 routes with service, 24 bunching routes, 25 gap routes, and 79 publisher alerts at the sampled instant.

## 2026-09-10 — ML program recovery and data-audit gates

- Migrated continuation to `currentHandoff.md`, persisted the four Luna roles and gates in `SUPERVISOR.md`, and hardened destructive integration tests so only an explicitly named disposable test database can be truncated; Gate 0 Software/QA Luna PASS.
- Added a reproducible read-only database audit and official external-source register. Snapshot evidence found 729,477 observations across 51.910 elapsed hours but only about 19.76 observed hours, with two long outages and 97.7971% exact static trip/stop-sequence eligibility. Both Gate 1 ML and Transit Luna critics PASS the honest insufficient-history judgment and narrow consecutive-stop operational travel-time target.
- Added the first SQL-independent shape-projection/interpolated-crossing primitives with six synthetic tests (11 focused tests total). No dataset, baseline, deep model, simulator, or optimizer is claimed; Gate 2 awaits a bounded service-day-aware database extractor, quality census, manifest, and real smoke artifact.
