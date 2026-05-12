@echo off
setlocal

echo ============================================================
echo  BIST DOM Replay - Setup and Launch
echo ============================================================
echo.

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo.
    echo Please install Python 3.10 or newer from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo [OK] Python found:
python --version
echo.

:: Check pip
pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip not found. Please reinstall Python with pip included.
    pause
    exit /b 1
)

:: Install / upgrade dependencies
echo Installing dependencies from requirements.txt ...
echo.
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to install one or more packages.
    echo Try running this script as Administrator or check your internet connection.
    pause
    exit /b 1
)

echo.
echo [OK] All dependencies installed.
echo.

:: Verify critical imports before launching
python -c "from PyQt5.QtWidgets import QApplication; import pandas; import pyarrow; import numpy; print('[OK] All imports verified.')"
if errorlevel 1 (
    echo.
    echo [ERROR] Import verification failed. See error above.
    pause
    exit /b 1
)

echo.
echo Launching BIST DOM Replay ...
echo.
python bist_dom_replay.py

endlocal
