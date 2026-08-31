@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-console.ps1" -Stop %*
if errorlevel 1 (
  echo.
  echo JobAgent failed to stop cleanly. See the message above.
  pause
)
endlocal
