@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    call "%~dp0SetupEnvironment.bat"
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -B -X utf8 -m prototype
if errorlevel 1 (
    echo.
    echo [PlayGame] Game launch failed. The error is shown above.
    pause
    exit /b 1
)
