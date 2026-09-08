@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"
set "WEB_DIR=apps\web"
set "LOG_DIR=.transitpulse-logs"

if not exist "%PYTHON%" (
  echo.
  echo TransitPulse could not start.
  echo Python environment not found at %PYTHON%.
  echo Create it with: py -3 -m venv .venv
  goto :error
)

"%PYTHON%" -c "import fastapi, sqlalchemy, google.transit.gtfs_realtime_pb2" >nul 2>nul
if errorlevel 1 (
  echo.
  echo TransitPulse could not start.
  echo Required Python dependencies are missing from .venv.
  echo Run: .venv\Scripts\python.exe -m pip install -e "apps/api[dev]"
  goto :error
)

"%PYTHON%" -m transitpulse_api.launch_checks database
if errorlevel 1 goto :database_error

"%PYTHON%" -m alembic upgrade head
if errorlevel 1 (
  echo.
  echo TransitPulse could not apply its database migrations.
  goto :error
)

"%PYTHON%" -m transitpulse_api.launch_checks feed
if errorlevel 1 (
  echo.
  echo No imported GTFS feed was found. Downloading the configured official Edmonton feed...
  "%PYTHON%" -m transitpulse_api.import_gtfs
  if errorlevel 1 (
    echo TransitPulse could not import the configured GTFS feed.
    goto :error
  )
)

if not exist "%WEB_DIR%\node_modules" (
  echo Installing frontend dependencies...
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

powershell -NoProfile -Command "if (-not (Test-NetConnection -ComputerName localhost -Port 8000 -InformationLevel Quiet)) { exit 1 }" >nul 2>nul
if errorlevel 1 start "TransitPulse API" /min cmd /c "%PYTHON% -m uvicorn transitpulse_api.main:app --app-dir apps/api --host 127.0.0.1 --port 8000 > %LOG_DIR%\api.log 2>&1"

powershell -NoProfile -Command "if (-not (Test-NetConnection -ComputerName localhost -Port 3000 -InformationLevel Quiet)) { exit 1 }" >nul 2>nul
if errorlevel 1 start "TransitPulse Web" /min cmd /c "cd /d %WEB_DIR% && npm run dev > ..\..\%LOG_DIR%\web.log 2>&1"

start "TransitPulse Realtime Recorder" /min cmd /c "%PYTHON% -m transitpulse_api.realtime_recorder > %LOG_DIR%\realtime.log 2>&1"

echo Waiting for TransitPulse at http://localhost:3000 ...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(60); while((Get-Date) -lt $deadline){ try { if((Invoke-WebRequest -UseBasicParsing http://localhost:3000 -TimeoutSec 2).StatusCode -eq 200){ exit 0 } } catch {}; Start-Sleep -Seconds 1 }; exit 1"
if errorlevel 1 (
  echo TransitPulse frontend did not become available within 60 seconds.
  echo See %LOG_DIR%\api.log and %LOG_DIR%\web.log.
  goto :error
)

start "" "http://localhost:3000"
echo TransitPulse is running. This window can be closed; the application services stay active.
exit /b 0

:database_error
echo.
echo TransitPulse could not start.
echo See README setup section for PostgreSQL and PostGIS prerequisites.

:error
echo.
pause
exit /b 1
