@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem TransitPulse launcher.
rem   PostgreSQL + PostGIS check -> migrations -> static feed check -> API ->
rem   web app -> realtime recorder -> browser.
rem Every service that is already running is reused, never duplicated.
rem Set TRANSITPULSE_NO_BROWSER=1 to skip opening the browser.
rem This file must keep CRLF line endings (see .gitattributes): cmd.exe can
rem mis-resolve "goto :label" in LF-only batch files.
rem ---------------------------------------------------------------------------

set "PYTHON=.venv\Scripts\python.exe"
set "WEB_DIR=apps\web"
set "LOG_DIR=.transitpulse-logs"

echo.
echo TransitPulse
echo ============

if not exist "%PYTHON%" (
  echo.
  echo TransitPulse could not start.
  echo Python environment not found at %PYTHON%.
  echo Create it with: py -3 -m venv .venv
  echo Then run:      .venv\Scripts\python.exe -m pip install -e "apps/api[dev]"
  goto :error
)

"%PYTHON%" -c "import fastapi, sqlalchemy, numpy, sklearn, google.transit.gtfs_realtime_pb2" >nul 2>nul
if errorlevel 1 (
  echo.
  echo TransitPulse could not start.
  echo Required Python dependencies are missing from .venv.
  echo Run: .venv\Scripts\python.exe -m pip install -e "apps/api[dev]"
  goto :error
)

where npm >nul 2>nul
if errorlevel 1 (
  echo.
  echo TransitPulse could not start.
  echo Node.js and npm were not found on PATH. Install Node.js 22 or newer, then retry.
  goto :error
)

echo [1/6] Checking PostgreSQL and PostGIS ...
"%PYTHON%" -m transitpulse_api.launch_checks server
if errorlevel 1 goto :postgresql_error

"%PYTHON%" -m transitpulse_api.launch_checks postgis-files
if errorlevel 1 goto :postgis_files_error

echo [2/6] Applying database migrations ...
"%PYTHON%" -m alembic upgrade head
if errorlevel 1 goto :migrations_error

"%PYTHON%" -m transitpulse_api.launch_checks postgis-active
if errorlevel 1 goto :postgis_active_error

echo [3/6] Checking the imported GTFS feed ...
"%PYTHON%" -m transitpulse_api.launch_checks feed
if errorlevel 1 (
  echo No imported GTFS feed was found. Downloading the configured official Edmonton feed ...
  "%PYTHON%" -m transitpulse_api.import_gtfs
  if errorlevel 1 goto :gtfs_import_error
)

if not exist "%WEB_DIR%\node_modules" (
  echo Installing frontend dependencies ...
  pushd "%WEB_DIR%"
  call npm ci
  if errorlevel 1 (
    popd
    echo TransitPulse could not install frontend dependencies.
    goto :error
  )
  popd
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [4/6] Starting the API ...
rem A listening socket is checked directly. Test-NetConnection resolves
rem "localhost" to ::1 first and can miss an API bound to 127.0.0.1, which
rem started a second API that then failed to bind.
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if errorlevel 1 (
  start "TransitPulse API" /min cmd /c "%PYTHON% -m uvicorn transitpulse_api.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 > %LOG_DIR%\api.log 2>&1"
) else (
  echo       An API is already listening on port 8000; reusing it.
  echo       If you changed API code, stop that process first so the new code is served.
)

echo       Waiting for http://127.0.0.1:8000/health/db ...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(45); while((Get-Date) -lt $deadline){ try { $request=[System.Net.HttpWebRequest]::Create('http://127.0.0.1:8000/health/db'); $request.Proxy=$null; $request.Timeout=2000; $response=$request.GetResponse(); $status=[int]$response.StatusCode; $response.Close(); if($status -eq 200){ exit 0 } } catch {}; Start-Sleep -Seconds 1 }; exit 1"
if errorlevel 1 (
  echo TransitPulse API did not become available within 45 seconds.
  echo See %LOG_DIR%\api.log.
  goto :backend_launch_error
)

rem Loading the SCENARIOS estimator imports scikit-learn, which can take
rem several seconds on a cold disk cache. Doing it now means the first visit to
rem SCENARIOS opens immediately instead of sitting on a loading state.
echo       Preparing the SCENARIOS estimator ...
powershell -NoProfile -Command "try { $request=[System.Net.HttpWebRequest]::Create('http://127.0.0.1:8000/api/scenarios/model'); $request.Proxy=$null; $request.Timeout=90000; $response=$request.GetResponse(); $response.Close(); exit 0 } catch { exit 1 }" >nul 2>nul
if errorlevel 1 echo       The SCENARIOS estimator is not available; LIVE, REPLAY and ANALYTICS are unaffected. See README.

echo [5/6] Starting the web app ...
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if errorlevel 1 (
  start "TransitPulse Web" /min cmd /c "cd /d %WEB_DIR% && npm run dev > ..\..\%LOG_DIR%\web.log 2>&1"
) else (
  echo       The web app is already listening on port 3000; reusing it.
)

echo [6/6] Starting the realtime recorder ...
rem Only one recorder may poll the feeds: a second copy doubles the load on the
rem publisher and races the first for the same observation keys.
powershell -NoProfile -Command "$running = Get-CimInstance Win32_Process -Filter 'Name=''python.exe''' | Where-Object { $_.CommandLine -like '*transitpulse_api.realtime_recorder*' }; if ($running) { exit 0 } else { exit 1 }" >nul 2>nul
if errorlevel 1 (
  start "TransitPulse Realtime Recorder" /min cmd /c "%PYTHON% -m transitpulse_api.realtime_recorder --log-file %LOG_DIR%\realtime.log"
) else (
  echo       A realtime recorder is already running; reusing it.
)

echo       Waiting for http://localhost:3000 ...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(90); while((Get-Date) -lt $deadline){ try { $request=[System.Net.HttpWebRequest]::Create('http://127.0.0.1:3000/'); $request.Proxy=$null; $request.Timeout=5000; $response=$request.GetResponse(); $status=[int]$response.StatusCode; $response.Close(); if($status -eq 200){ exit 0 } } catch {}; Start-Sleep -Seconds 1 }; exit 1"
if errorlevel 1 (
  echo TransitPulse web app did not become available within 90 seconds.
  echo See %LOG_DIR%\web.log.
  goto :frontend_launch_error
)

if not defined TRANSITPULSE_NO_BROWSER start "" "http://localhost:3000"
echo.
echo TransitPulse is running at http://localhost:3000
echo This window can be closed; the services keep running in their own minimized windows.
exit /b 0

:postgresql_error
echo.
echo TransitPulse could not start.
echo PostgreSQL is unavailable. Start the configured PostgreSQL server, then retry.
echo The connection comes from TRANSITPULSE_DATABASE_URL in .env (see .env.example).
goto :error

:postgis_files_error
echo.
echo TransitPulse could not start.
echo PostGIS extension files are unavailable. Install the PostGIS bundle that matches PostgreSQL, then retry.
goto :error

:migrations_error
echo.
echo TransitPulse could not start because database migrations failed.
echo Check database permissions and the migration output above. PostGIS is enabled by migration 0001; no manual CREATE EXTENSION is required.
goto :error

:postgis_active_error
echo.
echo TransitPulse could not start because PostGIS is not active after migrations.
echo Check the migration output above and PostgreSQL extension permissions.
goto :error

:gtfs_import_error
echo.
echo TransitPulse could not start because the official Edmonton GTFS import failed.
echo Check the internet connection, or set TRANSITPULSE_GTFS_SOURCE_URL in .env.
goto :error

:backend_launch_error
echo.
echo TransitPulse could not start because the backend launch failed.
goto :error

:frontend_launch_error
echo.
echo TransitPulse could not start because the frontend launch failed.
goto :error

:error
echo.
if not defined TRANSITPULSE_NO_BROWSER pause
exit /b 1
