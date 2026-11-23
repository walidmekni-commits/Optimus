@echo off
REM Updates OPTIMUS from the newest plaxis-ai-automation*.zip in your Downloads folder (or the zip given:
REM update.bat "C:\path\plaxis-ai-automation.zip"). The old program files are replaced and removed; your projects,
REM settings and PLAXIS password (data folder, automation.db, .env) are kept. Close OPTIMUS first.
cd /d "%~dp0"
set "ZIP=%~1"
if "%ZIP%"=="" for /f "delims=" %%F in ('dir /b /o-d "%USERPROFILE%\Downloads\plaxis-ai-automation*.zip" 2^>nul') do if not defined ZIP set "ZIP=%USERPROFILE%\Downloads\%%F"
if not defined ZIP (
  echo No plaxis-ai-automation zip found in %USERPROFILE%\Downloads. Download it first, or drag the zip onto update.bat.
  pause
  exit /b 1
)
echo Updating OPTIMUS from "%ZIP%"
taskkill /fi "WINDOWTITLE eq OPTIMUS server*" /t /f >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*app.serve*' -or $_.CommandLine -like '*uvicorn app.main*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
set "TMPX=%TEMP%\optimus-update"
if exist "%TMPX%" rmdir /s /q "%TMPX%"
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%ZIP%' -DestinationPath '%TMPX%' -Force"
if not exist "%TMPX%\plaxis-ai-automation\run.bat" (
  echo The zip does not contain the OPTIMUS folder plaxis-ai-automation.
  pause
  exit /b 1
)
REM mirror the new version over this folder, except your data
robocopy "%TMPX%\plaxis-ai-automation" "%~dp0." /MIR /XD data runs /XF automation.db .env update.bat /NFL /NDL /NJH /NJS /NP
rmdir /s /q "%TMPX%"
echo.
echo OPTIMUS is updated. Start it with run.bat (or the OPTIMUS shortcut).
pause
