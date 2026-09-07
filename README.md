# TransitPulse

Foundation for TransitPulse, a future public-transit analytics platform. This milestone deliberately contains only a frontend, API, and PostGIS-backed connectivity check—no GTFS or analytics schema.

## Run locally

Prerequisite: Docker Desktop with Docker Compose enabled.

```sh
docker compose up --build
```

Open <http://localhost:3000>. The page calls the FastAPI service through the frontend proxy and displays both API and database/PostGIS status.

Useful endpoints:

```text
http://localhost:8000/health
http://localhost:8000/health/db
```

Compose automatically runs `alembic upgrade head` before starting the API. It uses development-only default credentials; optionally copy `.env.example` to `.env` to override them.

Stop the stack with `docker compose down`. Use `docker compose down -v` only when you intentionally want to remove the local database volume.

## Tests

The backend integration tests require a real PostGIS database and never mock it. With the stack running:

```sh
docker compose exec api pytest
```

The CI workflow starts an isolated PostGIS service, applies migrations, runs those tests, and type-checks/builds the frontend.

## Layout

```text
apps/api/       FastAPI application and tests
apps/web/       Next.js frontend
migrations/     Alembic migration history
docs/           concise architecture notes
```

See [docs/foundation.md](docs/foundation.md) for the M0 boundary and runtime request path.
