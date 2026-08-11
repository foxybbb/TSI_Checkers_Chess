@echo off
:: ============================================================================
::  Checkers - Robotic Board
::  Runs the setup window, then the game against the physical board / UR3 robot.
:: ============================================================================
cd /d "%~dp0"

:: Use a local virtual-env if one exists, otherwise the Python on PATH.
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

%PY% main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo The game exited with an error ^(code %ERRORLEVEL%^).
    echo If this is the first run, install dependencies with:
    echo     %PY% -m pip install -r requirements.txt
    pause
)
