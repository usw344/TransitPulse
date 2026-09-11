# TransitPulse current handoff

## Current phase

- Phase 2: route/trip/shape-aware segment dataset implementation is next. Documentation/recovery and data-audit/model-target phases are complete; M0 product checkpoint and M1 runtime/recorder recovery are reached.
- The program direction is a learned Edmonton transit digital twin followed by constrained operational optimization. Work must advance through the gates in `SUPERVISOR.md`; later phases may not be claimed before their prerequisites pass.

## Current product state

- Existing product: Edmonton LIVE, REPLAY, and ANALYTICS implementation is present in a dirty working tree. Verified ML recovery/audit foundation is committed at `0e585a5`; tested segment-label primitives at `6bac0c6`; the finalized handoff at `2f36a66`; and the run archive through `c743b77`. Check `git status --short --branch` for the live ahead count.
- Do not discard or overwrite the pre-existing uncommitted application/test changes. They are the user's work/current product continuation state.
- MODEL LAB has not been implemented and must not be represented as real yet.
- M0 — REACHED. Independent Luna Software/QA PASS: `git diff --check` clean; full API suite is 51 passed against explicit disposable `transitpulse_test` PostGIS after migration; frontend typecheck/build pass; analytics tests 3 passed; replay tests 5 passed; no secrets/generated artifacts found. The test fixture now keeps the approved disposable URL credential only in memory rather than converting it to masked `***`. Migration 0005 guards its downgrade against legitimate null stop locations and requires explicit remediation.
- M1 — REACHED. At 2026-09-10 21:07 America/Regina, the supported launcher (run with network permission) verified PostgreSQL, PostGIS, migrations, and GTFS, then started API PID 15712 and recorder PID 21928 hidden. Windows denied command-line inspection, so process roles are evidenced by launcher order, API HTTP 200, and recorder log/status behavior. The prior failed recorder was PID 24916 (command line unavailable; it remained alive after API PID 17260 was stopped and kept reporting socket error 10013); it was terminated before launcher recovery. One short-lived attempted replacement, PID 34996, exited because the old process held `realtime.log` open. The existing `transitpulse_api.realtime_recorder` entry point now uses bounded rotation when given `--log-file`: 2,000,000 bytes plus 3 backups. Successful ETS polls at 21:07:57, 21:08:29, 21:09:00, and 21:09:32 reported 264 vehicle positions, 1,196/1,185/1,187/1,187 trip updates, and 90 alerts with no status errors. Immutable observations advanced 821,782 at 21:07:25 → 822,046 at 21:08:24 → 822,574 at 21:09:25. API `/health/db` and web each returned HTTP 200 after the second interval. `\.transitpulse-logs\realtime.log` is ignored and bounded by rotation.

## Current architecture

- FastAPI + SQLAlchemy/PostgreSQL/PostGIS backend under `apps/api`.
- Next.js frontend under `apps/web`.
- Alembic migrations under `migrations`.
- Immutable `vehicle_observations` are associated with the static GTFS feed active at capture time.
- ML dataset, model, simulator, and optimizer packages do not yet exist.

## Important paths

- `currentHandoff.md`: primary crash-recovery/current-state document.
- `SUPERVISOR.md`: permanent independent Luna critic roles, evidence rules, and gates.
- `WORKLOG.md`: historical archive; do not read at normal startup and append once only at a normal run end.
- `apps/api/transitpulse_api/models.py`: GTFS, realtime state, and observation schema.
- `apps/api/transitpulse_api/realtime_recorder.py`: observation recorder.
- `apps/api/tests`: API/database tests; tests must remain isolated from production data.
- `artifacts/` (to be created/ignored): generated datasets, experiments, checkpoints, and reports.

## Database / data state

- Persisted audit `artifacts/data-audits/latest.json` found observations from 2026-09-07 20:42:40 through 2026-09-10 00:37:15 America/Regina (51.910 elapsed hours), but only 23 partially covered clock-hour buckets across four calendar dates and two global outages of about 13.5 and 18.6 hours. This is not four full days and is not credible deep-model coverage.
- At audit time the table held 729,477 observations, 230 routes, 7,562 trips, 875 vehicles, and one static-feed version. Static route/trip matches were 727,321 (99.7044%); exact trip/stop-sequence matches were 713,407 (97.7971%). All nonmatches are excluded before label extraction. The observation relation plus indexes was about 371 MiB and the database about 867 MiB.
- Positive per-vehicle interval percentiles were P10/P25/P50/P75/P90/P95/P99 = 30/30/30/31/45/60/76 seconds. Nonpositive intervals require duplicate/same-timestamp handling in the later label-quality pipeline.
- Canonical persisted audit tooling is `transitpulse_ml.audit`, launched from the repository root by `scripts/audit_ml_data.py`.
- Raw historical records are production evidence and must not be mutated by ML preprocessing or tests.

## ML dataset state

- Gate 1 approved target: directed consecutive-scheduled-stop arrival-to-arrival travel time on a feed/trip/route variant, inferred by shape projection/interpolated crossings and using only information available at prediction time.
- `transitpulse_ml.segments` now contains SQL-independent polyline projection and traversal-label primitives. Synthetic tests cover sparse GTFS sequences, interpolation, deterministic duplicate timestamps, unmatched sequences, vehicle-progress regression, excessive bracketing gaps, and basic lateral projection.
- This is not yet a model-ready dataset. No database streaming/run grouping, service-day/midnight handling, ambiguity census, dataset ID/manifest, split, or persisted smoke rows exist. Gate 2 is not ready for review.

## Current model / experiment state

- No verified baseline or deep-model experiment exists.
- Deep learning is blocked until the data audit, segment-label pipeline, chronological split, and strong baselines pass their gates.

## Simulator state

- Not started; blocked on a credible held-out travel-time model and validation plan.

## Optimizer state

- Not started; blocked on digital-twin validation.
- Initial scope is constrained operational planning (headways, departures, fleet allocation, recovery), not unconstrained route redesign.
- Vehicle-position history is not passenger-demand data. Passenger claims require separate credible demand evidence; otherwise waiting-time measures are labeled proxies.

## Verification state

- Gate 0 DOCUMENTATION / RECOVERY: PASS (independent Software/QA Luna; documentation accuracy, liveness/restart instructions, and destructive-test isolation verified after four review cycles).
- Gate 1 DATA AUDIT / MODEL TARGET: PASS (independent ML Luna and Transit Luna; reproducible sparse-coverage judgment, static referential eligibility, external-source limitations, and narrow operational target verified).
- Gates 2–7: not reached.
- Test isolation guard: 5 focused unit tests pass; health integration tests skip without an explicit `*_test`/`test_*` database URL. The former destructive opt-in bypass was removed and CI now provisions `transitpulse_test`.
- Phase 2 primitives: 6 segment tests pass; combined audit/safety/segment suite is 11 passed. No Gate 2 critic review has been requested because required real extraction evidence is absent.

## Known problems

- The working tree contains substantial pre-existing uncommitted LIVE/REPLAY/ANALYTICS changes; commits must avoid accidentally misattributing or losing them.
- Observation history is sparse and discontinuous, with fewer than 30 usable days; deep learning is not currently justified.
- PostgreSQL, API, frontend, and exactly one network-enabled recorder are currently live after supported launcher recovery. The initial sandboxed recorder source failures are resolved; raw observations are advancing. No ML training or optimization job has been launched by this run.
- Migration 0005 cannot safely be downgraded while coordinate-less stops exist; it now fails with a remediation instruction rather than modifying/deleting valid source data. The safe product checkpoint has passed its independent review and is ready to commit.
- Segment primitives do not yet prove loop/repeated-stop ambiguity handling, service-day identity for after-midnight trips, terminal/layover exclusion, or scalable database extraction. Do not treat them as a dataset pipeline.

## Failed approaches not to repeat

- Never run tests or preprocessing that truncate or mutate the only application database.
- Never use random row splits for time-series evaluation.
- Never treat future/downstream observations as prediction-time features.
- Never claim vehicle locations represent passenger demand.
- Never advance to optimization before quantitative held-out simulator validation.

## Exact next action

1. Commit the reviewed M0/M1 product and recovery checkpoint without including generated logs or artifacts.
2. Keep M1's bounded, network-enabled recorder running; check observation coverage again at the next ML milestone.
3. Add a bounded, read-only, streaming database extractor around `transitpulse_ml.segments`, including service-day identity, ambiguity/terminal rejection, quality counters, and a persisted small smoke dataset. Do not train or split models before Gate 2 ML and Transit Luna PASS.

## Commands to continue

```powershell
Get-Content -Raw currentHandoff.md
Get-Content -Raw SUPERVISOR.md
git status --short --branch
.\.venv\Scripts\python.exe scripts/audit_ml_data.py --output artifacts/data-audits/latest.json
Get-Content -Raw artifacts/data-audits/latest.json
.\.venv\Scripts\python.exe -m pytest apps/api/tests/test_ml_segments.py apps/api/tests/test_ml_audit.py apps/api/tests/test_database_safety.py -q
```

Do not read all of `WORKLOG.md` unless recovery genuinely requires historical context.

Process/liveness checks:

```powershell
Get-Process python,node,postgres -ErrorAction SilentlyContinue | Select-Object Id,ProcessName,StartTime,Path
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'apps/api'); from sqlalchemy import text; from transitpulse_api.database import get_engine; c=get_engine().connect(); c.execute(text('SET TRANSACTION READ ONLY')); print(c.execute(text('select count(*), max(observed_at) from vehicle_observations')).one()); c.rollback(); c.close()"
```

Run the second command twice at least 35 seconds apart to infer whether the 30-second recorder is advancing. Check product liveness with `Invoke-WebRequest -UseBasicParsing http://127.0.0.1:3000` and `Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health/db`. At the last check the web server was live and the API was not listening.

## Supervisor / critic state

- Independent critics must be separate Luna agent contexts when available.
- Gate 0 Software/QA Luna: PASS.
- Gate 1 ML Luna: PASS. Gate 1 Transit Luna: PASS after static referential-match counts and exclusions were added.
- Current unresolved review: Gate 2 has not been implemented or reviewed.
