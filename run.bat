@echo off
REM OPTIMUS: one-click start for Windows. Installs what is needed (first time), starts the server in a
REM minimized window and opens the app in its own window (no address bar, no tabs), like a desktop program.
REM Closing the OPTIMUS window never stops a PLAXIS run: the server stops by itself once nothing is open or running.
cd /d "%~dp0"
title OPTIMUS
if not exist .env copy .env.example .env >nul
REM the requirements are installed only the first time and after an update (no wait at each start)
python -m app.req_check
if not errorlevel 1 goto ready
python -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo Retrying without the optional fast PDF reader...
  findstr /v /i "pymupdf" requirements.txt > "%TEMP%\plaxis-req.txt"
  python -m pip install -q --disable-pip-version-check -r "%TEMP%\plaxis-req.txt"
)
if errorlevel 1 (
  echo.
  echo Could not install the requirements. Is Python installed and on PATH? Try: py -m pip install -r requirements.txt
  pause
  exit /b 1
)
python -m app.req_check --done
:ready
set "URL=http://127.0.0.1:8000"
REM pythonw (next to python.exe): the server without a console window; set here, outside the blocks below, because
REM cmd reads a ( ... ) block in one go and would not see a value set inside it
set "PYW="
for /f "delims=" %%P in ('python -m app.pyw') do set "PYW=%%P"
if not defined PYW set "PYW=python"
REM already running (a second double-click, or a run still calculating): only open the window
python -m app.wait_ready %URL% 2 >nul 2>&1
if errorlevel 1 (
  REM the server: errors are written to data\logs\optimus-server.log, it restarts itself after an error and it
  REM stops by itself when no OPTIMUS window is open and no PLAXIS run is in progress
  start "OPTIMUS server" /min "%PYW%" -m app.serve 8000
  echo Starting OPTIMUS...
  python -m app.wait_ready %URL%
  if errorlevel 1 (
    echo.
    echo OPTIMUS did not start. The reason is in the server log:
    python -c "from app.config import data_dir; print(data_dir() / 'logs' / 'optimus-server.log')"
    pause
    exit /b 1
  )
)
REM its own program window (Edge / Chrome in application mode, found through the registry), never a browser tab
python -m app.launch %URL%
exit /b 0
