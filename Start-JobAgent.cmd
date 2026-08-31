@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-console.ps1" %*
if errorlevel 1 (
  echo.
  echo JobAgent failed to start. See the message above.
  pause
)
endlocal
