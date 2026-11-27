@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    call "%~dp0SetupEnvironment.bat"
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
start "" ".venv\Scripts\pythonw.exe" -B -X utf8 -m prototype
