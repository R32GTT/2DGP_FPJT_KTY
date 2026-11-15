@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo See prototype\README.md to prepare the virtual environment.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -B -X utf8 -m prototype %*
if errorlevel 1 pause
