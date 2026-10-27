@echo off
rem Dev alias: same as Cosmolayor.cmd but does NOT open the browser.
cd /d "%~dp0"
set "PY=%~dp0python\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
title Cosmolayor (no auto-open) - http://127.0.0.1:8765/control
"%PY%" -u server.py %*
echo.
echo Server stopped.
pause
