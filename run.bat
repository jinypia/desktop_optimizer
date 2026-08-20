@echo off
rem Launch Desktop Optimizer without a console window, using the project venv.
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
