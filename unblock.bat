@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo.
echo   Unblock downloaded files (remove Windows "Mark of the Web")
echo   ----------------------------------------
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-ChildItem -LiteralPath '%cd%' -Recurse -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue; Write-Host 'Done. You can now double-click start.bat.'"

echo.
pause
