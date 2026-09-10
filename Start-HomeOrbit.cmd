@echo off
where pwsh.exe >nul 2>nul
if errorlevel 1 (
    echo PowerShell 7 is required. Install it and retry.
    pause
    exit /b 1
)
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts/Start-HomeOrbit.ps1"
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "http://127.0.0.1:3000"
