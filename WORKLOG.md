# TransitPulse worklog

<!-- Append compact, factual handoff entries below. -->

## 2026-09-07 — M0 foundation

- Added Docker Compose `db` (PostGIS), `api` (FastAPI/SQLAlchemy 2), `web` (Next/React/TS/Tailwind); web rewrites `/api/*` to API, API has `/health` and real-query `/health/db`.
- Alembic is root-owned; `0001_enable_postgis` creates only `postgis` extension. No transit domain schema/data exists. DB config is `TRANSITPULSE_DATABASE_URL`.
- Added real-DB/PostGIS pytest coverage and CI PostGIS service + migration + frontend typecheck/build. Docker API image includes test dependencies for `docker compose exec api pytest`.
- Verified: `npm ci`, `npm run typecheck`, `npm run build` (Next 16.3.3); API health pytest (1 passed); `alembic upgrade head --sql` emits extension SQL; `compileall`; `git diff --check`.
- Known runtime gap: this host has no `docker`/Podman/local PostgreSQL and port 5432 is closed; Compose launch and the two real PostGIS tests were not executable here. Do not treat them as host-verified.
- Next: on a Docker-capable host run `docker compose up --build`, inspect web status, then `docker compose exec api pytest`; after M0 runtime verification, begin M1 GTFS static-feed domain/import design.
