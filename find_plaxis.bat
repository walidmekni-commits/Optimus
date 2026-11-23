@echo off
REM Finds PLAXIS' plxscripting folder and prints what to paste into the app.
echo Searching for plxscripting (this can take a minute)...
echo.
set FOUND=
for %%R in ("C:\Program Files" "C:\ProgramData" "C:\Program Files (x86)") do (
  for /f "delims=" %%F in ('where /r %%R easy.py 2^>nul ^| findstr /i "\\plxscripting\\easy.py"') do (
    set FOUND=1
    echo Found: %%F
    for %%P in ("%%~dpF..") do echo   Paste into "plxscripting folder":  %%~fP
    echo.
  )
)
if not defined FOUND (
  echo Nothing found. Is PLAXIS installed on this PC?
  echo Last resort: python -m pip install plxscripting
)
echo.
pause
