@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch-console.ps1" -SingleProcess %*
if errorlevel 1 (
  echo.
  echo JobAgent commercial runtime failed to start. See the message above.
  pause
)
endlocal
