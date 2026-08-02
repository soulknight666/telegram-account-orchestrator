@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TAM

REM If Windows shows "Unknown publisher", click Run.
REM Or run unblock.bat once to clear the download flag.

echo.
echo   Telegram Account Manager
echo   ----------------------------------------
echo   One-click setup and start.
echo   Choose local/server and web/bot in the next steps.
echo   Stop: Ctrl+C
echo   ----------------------------------------
echo.

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_py
where python >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_python
goto no_python

:use_py
py -3 setup.py --auto %*
goto end

:use_python
python setup.py --auto %*
goto end

:no_python
echo.
echo   [X] Python not found.
echo   Install from https://www.python.org/downloads/
echo   Check "Add python.exe to PATH" on the first screen.
echo.
pause
exit /b 1

:end
echo.
pause
