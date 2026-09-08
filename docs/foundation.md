# TransitPulse foundation

TransitPulse remains a modular monolith: browser → Next.js `/api/*` rewrite → FastAPI → PostgreSQL/PostGIS. Alembic owns the schema. Static GTFS data is immutable per feed version, and API queries resolve either the newest successful feed or an explicit historical `feed_id`.

The first user-facing slice is the MapLibre Edmonton network viewer. It fetches the background route geometry once and requests a route’s detail, stops, and shape only after selection, leaving room for future realtime map sources without a frontend rewrite.
