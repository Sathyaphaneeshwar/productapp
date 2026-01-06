@echo off
REM Double-click this file to start the Stock App
REM This will open PowerShell and run the start script

echo Starting Stock App...
powershell -ExecutionPolicy Bypass -File "%~dp0start_app.ps1"
pause
