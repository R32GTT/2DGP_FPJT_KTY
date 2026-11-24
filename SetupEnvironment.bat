@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1

if exist ".venv\Scripts\python.exe" goto install

echo [Setup] Creating the project virtual environment...
py -3.13 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if not errorlevel 1 (
    py -3.13 -m venv .venv
    if errorlevel 1 goto failed
    goto install
)
py -3.12 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if not errorlevel 1 (
    py -3.12 -m venv .venv
    if errorlevel 1 goto failed
    goto install
)
python -c "import sys, struct; assert (3, 12) <= sys.version_info[:2] <= (3, 13) and struct.calcsize('P') == 8" >nul 2>&1
if not errorlevel 1 (
    python -m venv .venv
    if errorlevel 1 goto failed
    goto install
)
echo [Setup] Install 64-bit Python 3.12 or 3.13, then run PlayPrototype.bat again.
goto failed

:install
".venv\Scripts\python.exe" -c "import sys, struct; assert (3, 12) <= sys.version_info[:2] <= (3, 13) and struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 (
    echo [Setup] The existing .venv is invalid or unsupported.
    echo [Setup] Rename .venv and retry with 64-bit Python 3.12 or 3.13 installed.
    goto failed
)
".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m ensurepip
    if errorlevel 1 goto failed
)
echo [Setup] Checking project dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-input -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip check
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -B -X utf8 -c "import tkinter, pico2d, py_trees, shapely, numpy, scipy; from navground import core; from PIL import Image"
if errorlevel 1 goto failed
popd
exit /b 0

:failed
echo [Setup] Environment setup failed. See the error above and retry.
echo [Setup] Internet access is required when dependencies need to be installed.
popd
exit /b 1
