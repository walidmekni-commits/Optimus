@echo off
REM Puts an OPTIMUS shortcut (with its icon) on the desktop and in the Start menu: double-click it to start OPTIMUS.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
  "  Remove-Item -ErrorAction SilentlyContinue (Join-Path $dir 'OPTIQUS.lnk');" ^
  "  $s = $ws.CreateShortcut((Join-Path $dir 'OPTIMUS.lnk'));" ^
  "  $s.TargetPath = '%~dp0run.bat'; $s.WorkingDirectory = '%~dp0'; $s.WindowStyle = 7;" ^
  "  $s.IconLocation = '%~dp0static\img\optimus.ico'; $s.Description = 'OPTIMUS - Engineering Optimization Engine (opens on the sign-in page)'; $s.Save() }"
echo OPTIMUS shortcut created on the desktop and in the Start menu.
pause
