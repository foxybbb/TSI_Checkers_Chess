@echo off
:: ============================================================================
::  Robotic Board Games - top-level launcher
::  Lets you choose Checkers or Chess to play against the physical board / robot.
:: ============================================================================
cd /d "%~dp0"

:: Prefer the Python launcher (py) on Windows, fall back to python on PATH.
where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py launcher.py
) else (
    python launcher.py
)

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo The launcher exited with an error.
    pause
)
