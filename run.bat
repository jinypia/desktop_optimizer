@echo off
rem Launch Desktop Optimizer without a console window.
rem Prefers a local-disk venv when present: running Qt from a network share
rem can freeze the app whenever the share hiccups (memory-mapped DLLs).
rem PYTHONDONTWRITEBYTECODE avoids stale-bytecode issues on network drives.
set PYTHONDONTWRITEBYTECODE=1
set LOCALVENV=%LOCALAPPDATA%\DesktopOptimizer\venv
if exist "%LOCALVENV%\Scripts\pythonw.exe" (
  start "" "%LOCALVENV%\Scripts\pythonw.exe" "%~dp0main.py"
) else (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
)
