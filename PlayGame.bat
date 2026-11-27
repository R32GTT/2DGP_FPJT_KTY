@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    call "%~dp0SetupEnvironment.bat"
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
wscript.exe "%~dp0PlayGame.vbs"
