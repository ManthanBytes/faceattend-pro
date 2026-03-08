@echo off
title AttendX Pro v3
cls
echo ================================================
echo   AttendX Pro — QR + Face + Timetable System
echo ================================================
python --version >nul 2>&1
if errorlevel 1 (echo Python not found! Download from python.org & pause & exit)
pip install flask -q
echo Starting...
start "" http://localhost:5000
python app.py
pause
