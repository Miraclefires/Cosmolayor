@echo off
rem Cosmolayor - double-click me. Starts the local server (console keeps the logs)
rem and opens the control panel in your browser. Close this window to stop.
cd /d "%~dp0"

rem 1) bundled interpreter (the release zip ships one)  2) local 3.12  3) PATH
set "PY=%~dp0python\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

title Cosmolayor - http://127.0.0.1:8765/control
"%PY%" -u server.py --open %*
echo.
echo Server stopped.
pause
