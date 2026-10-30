@echo off
cd /d "%~dp0"
if exist "..\..\server\setup_gdrive_oauth.py" (
    cd /d "%~dp0..\..\server"
)
title Authorize Google Drive 5TB Storage
echo ========================================================
echo   Enterprise AI Hub - Google Drive 5TB Authorization
echo ========================================================
echo.
python setup_gdrive_oauth.py
echo.
pause
