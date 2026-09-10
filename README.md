# TransitPulse

TransitPulse is an Edmonton transit data and operations platform evolving from a live/replay/analytics viewer into a validated learned digital twin for constrained service-plan optimization. It combines versioned static GTFS with official ETS GTFS-Realtime Vehicle Positions, Trip Updates, and Alerts plus immutable historical vehicle observations. Vehicle observations describe operations, not passenger demand; passenger outcomes remain proxies unless credible demand data is added.

## Project continuation

- [`currentHandoff.md`](currentHandoff.md) is the primary current-state and crash-recovery document. Read it first on every continuation.
- [`SUPERVISOR.md`](SUPERVISOR.md) permanently defines the independent Luna critic roles, scientific standards, evidence requirements, and phase gates. Read it second.
- [`WORKLOG.md`](WORKLOG.md) is a historical archive. Do not read it during normal startup or use it as scratch notes; append one concise entry only at a normal verified end-of-run.

The implementation order is data audit -> segment/model dataset -> chronological features/splits -> strong baselines -> justified deep model -> held-out digital-twin validation -> constrained operational optimizer -> evidence-backed Model Lab. A later phase must not be presented as real before its prerequisite critic gates pass.

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

Database-backed tests require an explicitly configured disposable PostgreSQL/PostGIS test database and skip when it is unavailable. Tests must never target the production history database. The history API enforces a 24-hour maximum query range and a 5,000-observation maximum; replay and analytics are implemented in the current working tree and remain subject to their existing verification gates.
