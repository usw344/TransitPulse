# M0 foundation

TransitPulse is a modular monolith. The initial runtime consists of one Next.js frontend, one FastAPI application, and one PostgreSQL/PostGIS database. There are no domain modules or transit data tables yet.

```text
Browser -> Next.js (/api/* rewrite) -> FastAPI -> PostgreSQL + PostGIS
```

The frontend calls same-origin `/api/health` and `/api/health/db`; Next.js proxies those requests to the API using `API_BASE_URL`. In Compose that is the internal `api` service, so browser clients do not need database or API-container network access.

Alembic owns database evolution. The first revision enables the PostGIS extension only. Future transit schema changes should be additive Alembic revisions, kept in bounded modules within `apps/api/transitpulse_api` rather than split into services prematurely.
