@echo off
title Enterprise AI Hub Server
cd /d "%~dp0"
echo ===================================================
echo     Enterprise AI Hub - Backend Server
echo ===================================================
echo.
python -m uvicorn ui_app:app --host 127.0.0.1 --port 8000 --reload
pause
