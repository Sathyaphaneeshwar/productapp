@echo off
REM Double-click this file to stop the Stock App

echo Stopping Stock App...
powershell -ExecutionPolicy Bypass -File "%~dp0stop_app.ps1"
pause
