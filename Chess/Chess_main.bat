@echo off
:: Run from the folder this .bat lives in, using the local virtual env.
cd /d "%~dp0"
call ".venv\Scripts\activate.bat"
python "chess_main.py"
deactivate
pause
