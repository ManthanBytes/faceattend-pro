@echo off
title FaceAttend Pro V7
cls
echo ==========================================
echo   FaceAttend Pro V7 - Multi User System
echo ==========================================
echo.
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not installed!
    echo Download from: https://python.org
    echo CHECK "Add Python to PATH" during install!
    pause & exit
)
echo Installing Flask...
pip install flask -q
echo.
echo Starting server...
echo.
start "" http://localhost:5000
python app.py
pause
