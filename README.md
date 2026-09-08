# TransitPulse

TransitPulse is an Edmonton live transit operations viewer. It combines a versioned static GTFS import with official ETS GTFS-Realtime Vehicle Positions, Trip Updates, and Alerts, plus bounded historical vehicle recording.

## Start on Windows

Install a local PostgreSQL server with the matching PostGIS extension first, then create a `transitpulse` database and role (the development defaults are in `.env.example`). Double-click `START_TRANSITPULSE.bat` from the repository root. It checks PostGIS, migrates safely, imports the official static feed only when no successful feed exists, starts API/web/recorder processes, and opens `http://localhost:3000`.

If it reports that PostGIS is unavailable, install the PostGIS bundle matching PostgreSQL's major version. The launcher never recreates or clears a database automatically.

## Native development

Use local Python, Node/npm, and PostgreSQL with PostGIS. Copy `.env.example` to `.env` to override the database or official source URLs.

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m transitpulse_api.import_gtfs
.\.venv\Scripts\python.exe -m uvicorn transitpulse_api.main:app --app-dir apps/api --reload
```

In another shell:

```powershell
cd apps/web
npm install
npm run dev
```

Open `http://localhost:3000`. The frontend proxies `/api/*` to the FastAPI service; set `API_BASE_URL` if the API runs elsewhere.

In a third shell, record current state:

```powershell
.\.venv\Scripts\python.exe -m transitpulse_api.realtime_recorder
```

The importer downloads the official City of Edmonton / ETS GTFS ZIP by default. Realtime URLs, polling, freshness, and operations thresholds are configurable via `TRANSITPULSE_*` settings. Historical observations retain the imported static feed ID used at capture time.

## Checks

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head --sql
cd apps/api; ..\..\.venv\Scripts\python.exe -m pytest
cd apps/web; npm run typecheck; npm run build
```

Database-backed tests require the migrated native PostgreSQL/PostGIS instance; they skip when it is unavailable. The history API enforces a 24-hour maximum query range and a 5,000-observation maximum; replay is intentionally not implemented.
