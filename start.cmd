@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Please create .venv and install requirements.txt first.
    pause
    exit /b 1
)
start "AnkiGen" ".venv\Scripts\pythonw.exe" "main.py"
