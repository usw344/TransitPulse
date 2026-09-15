# TransitPulse modeling data audit — 2026-09-10

## Decision

**Not ready for a credible learned travel-time model or a 30-day run.** The database contains 51.910 hours of elapsed wall-clock span but only 23 represented clock-hour buckets and about 19.76 hours of within-bucket coverage. Seventeen buckets are near-full hours. Two outages account for approximately 13 hours 32 minutes and 18 hours 37 minutes. The four represented calendar dates are therefore not four full days.

Keep the recorder running. The current history is sufficient to build and deterministically smoke-test a segment-label/dataset pipeline, but model selection, deep-learning claims, route-simulation validation, and optimization remain blocked by broader chronological coverage and later gates.

## Provenance and reproducibility

- Audit command: `.\.venv\Scripts\python.exe scripts/audit_ml_data.py --output artifacts/data-audits/latest.json`
- Transaction: PostgreSQL `SET TRANSACTION READ ONLY`; aggregate SELECTs only.
- Artifact: `artifacts/data-audits/latest.json` (ignored, regenerable, atomically replaced).
- Artifact schema: 1.
- Report SHA-256 at this snapshot: `eff08bde2f750624794c7f82f38fdb15b26faa4e9bd2349e447e2f860da6c880`.
- Database values are a point-in-time snapshot. The recorder was observed advancing after this snapshot, so later runs should regenerate rather than repeat these counts as current.

## Observation inventory

| Measure | Audit result |
|---|---:|
| Earliest observation | 2026-09-07 20:42:40 America/Regina |
| Latest observation | 2026-09-10 00:37:15 America/Regina |
| Elapsed span | 186,875 seconds / 51.910 hours |
| Calendar dates represented | 4 (partial) |
| Clock-hour buckets represented | 23 |
| Sum of within-bucket first-to-last spans | about 19.76 hours |
| Near-full hour buckets | 17 |
| Observations | 729,477 |
| Distinct route IDs | 230 |
| Distinct trip IDs | 7,562 |
| Distinct vehicle IDs | 875 |
| Static feed versions represented | 1 |
| Null observation time / position / trip / route / stop sequence | 0 / 0 / 0 / 0 / 0 |

Static referential eligibility is narrower than field completeness:

- 727,321/729,477 observations (99.7044%) match both their route and trip ID inside the referenced static feed. The 2,156 nonmatches are excluded before label extraction.
- 713,407/729,477 observations (97.7971%) also match the exact `(static feed, trip, current_stop_sequence)` to a GTFS `stop_times` row. The remaining 16,070 are excluded before label extraction.
- 683,305 observations omit `current_status`. GTFS-Realtime omission semantics must be handled explicitly; omission is not evidence that the vehicle arrived or stopped.
- The database session time zone is recorded as `America/Regina`, and the report stores both source observation and recorder timestamp bounds.

The hourly coverage pattern is concentrated in evening hours on September 7, noon-to-late-evening on September 8, and evening-to-midnight on September 9/10. It does not cover repeated comparable morning, midday, peak, off-peak, weekday, or weekend periods, so a chronological held-out evaluation would not be representative.

## Gaps and cadence

Largest gaps between globally distinct observation timestamps:

| Gap end | Seconds | Approximate duration |
|---|---:|---:|
| 2026-09-09 17:54:40 | 67,002 | 18 h 36 m 42 s |
| 2026-09-08 12:15:44 | 48,721 | 13 h 32 m 01 s |
| 2026-09-08 12:20:31 | 287 | 4 m 47 s |
| 2026-09-09 17:56:35 | 115 | 1 m 55 s |
| 2026-09-08 21:43:25 | 109 | 1 m 49 s |

Per-vehicle consecutive-observation interval results:

- 728,602 intervals before quality filtering.
- Positive interval P10/P25/P50/P75/P90/P95/P99: 30/30/30/31/45/60/76 seconds.
- 12,988 nonpositive intervals. These are not silently valid traversal evidence; the segment-label pipeline must deterministically deduplicate/reject same-time or regressive observations.
- 1,391 intervals exceed two minutes and 778 exceed ten minutes. Long intervals cross missing vehicle updates, service breaks, or vehicle reappearance and must not be converted directly into segment travel labels.

## Static GTFS provenance

One successful City of Edmonton / ETS static feed was imported at 2026-09-07 20:42:24 America/Regina, with declared service dates 2026-09-02 through 2026-11-28. Feed ID is `c5c72535-fe84-4517-bc59-3c9e8e485c27`. All audited observations reference that feed. Later datasets must preserve the feed ID on every derived traversal because route variants, stop sequences, shapes, and schedules can change after a new import.

## Storage

- `vehicle_observations` heap: 166,191,104 bytes (about 158.5 MiB).
- Its indexes: 222,486,528 bytes (about 212.2 MiB).
- Table plus indexes: 388,751,360 bytes (about 370.7 MiB).
- Whole database: 909,031,103 bytes (about 866.9 MiB).

The existing feed/vehicle-time, feed/route-time, and spatial indexes support bounded derivation. Dataset generation must still stream/chunk by chronological window and must never materialize the full raw table into application memory merely because this early snapshot is under one gigabyte.

## First modeling target

Predict **elapsed arrival-to-arrival time for one directed pair of consecutive scheduled stops on a specific static-feed trip/route variant**, conditional on information available when the vehicle departs or is last observed at the upstream stop.

Initial labels should include dwell at the downstream stop only if the extraction boundary is explicitly defined and consistently measurable. The preferred first implementation should persist both arrival-to-arrival traversal time and confidence/quality metadata, then evaluate whether dwell can be separated without invented timestamps. It must reject ambiguous map matches, skipped/repeated stops, trip/feed changes, nonmonotonic stop progression, implausible speeds, long observation gaps, and terminal/layover transitions rather than forcing them into labels.

Only observations with matching static-feed route, trip, and exact stop sequence are eligible for target extraction. Non-null IDs alone are insufficient. This target supports a later operational simulator. It does not predict passenger demand, boardings, transfer behavior, actual passenger waiting time, route choice, or an optimal network.

## External evidence

The official-source investigation is recorded in [`external-data-register.md`](external-data-register.md). Weather and holidays are viable first context sources. Current road disruptions and service alerts are conditional candidates whose history/as-of semantics must be proven before use. Route report cards provide useful route-level plausibility/demand context but not stop-level APC or OD. No official public historical road-segment speed stream, timestamped collision stream, stop-level APC history, fare-card OD, or full current travel-demand matrix was established.

## Gate 1 claim

The claim submitted for automated internal validation review is limited to: the current data audit is reproducible and accurately reflects the recorded data; the history is sparse/discontinuous and insufficient for credible deep modeling; the recorder should continue; and the narrow directed consecutive-stop operational travel-time target is scientifically and operationally appropriate for building a smoke-test pipeline. No model-performance, route-simulation, passenger-optimal, or optimization claim is made.
