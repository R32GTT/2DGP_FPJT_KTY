@echo off
cd /d "%~dp0"
call "%~dp0SetupEnvironment.bat"
if errorlevel 1 (
    pause
    exit /b 1
)
if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -B -X utf8 -m prototype.launcher
    exit /b
)
".venv\Scripts\python.exe" -B -X utf8 -m prototype %*
if errorlevel 1 pause
