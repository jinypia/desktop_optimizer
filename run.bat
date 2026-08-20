@echo off
rem Launch Desktop Optimizer without a console window, using the project venv.
rem PYTHONDONTWRITEBYTECODE avoids stale-bytecode issues on network drives.
set PYTHONDONTWRITEBYTECODE=1
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
