# Start App Script for Windows
# Double-click this file or right-click -> "Run with PowerShell"
# This will start both backend and frontend and keep running

$Host.UI.RawUI.WindowTitle = "Stock App - Running"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$LogDir = Join-Path $ProjectRoot "logs"

# Create logs directory if it doesn't exist
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "       Starting Stock App              " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Find Python executable
$Python = $null
$PythonPaths = @(
    (Join-Path $BackendDir ".venv\Scripts\python.exe"),
    (Join-Path $BackendDir "backend_venv\Scripts\python.exe"),
    (Join-Path $ProjectRoot "venv\Scripts\python.exe"),
    "python"
)

foreach ($path in $PythonPaths) {
    if (Test-Path $path -ErrorAction SilentlyContinue) {
        $Python = $path
        break
    }
    if ($path -eq "python") {
        try {
            $null = & python --version 2>&1
            $Python = "python"
            break
        } catch {}
    }
}

if (-not $Python) {
    Write-Host "[ERROR] Python not found!" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[INFO] Using Python: $Python" -ForegroundColor Gray

# Start Backend
Write-Host ""
Write-Host "[BACKEND] Starting..." -ForegroundColor Yellow
$BackendLog = Join-Path $LogDir "backend.log"

$backendProcess = Start-Process -FilePath $Python `
    -ArgumentList "app.py" `
    -WorkingDirectory $BackendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $BackendLog `
    -RedirectStandardError (Join-Path $LogDir "backend_error.log") `
    -PassThru

Start-Sleep -Seconds 2

if ($backendProcess.HasExited) {
    Write-Host "[BACKEND] Failed to start! Check logs at: $BackendLog" -ForegroundColor Red
} else {
    Write-Host "[BACKEND] Started (PID: $($backendProcess.Id))" -ForegroundColor Green
    Write-Host "          Log: $BackendLog" -ForegroundColor Gray
}

# Start Frontend
Write-Host ""
Write-Host "[FRONTEND] Starting..." -ForegroundColor Yellow
$FrontendLog = Join-Path $LogDir "frontend.log"

# Check if node_modules exists
$NodeModules = Join-Path $FrontendDir "node_modules"
if (-not (Test-Path $NodeModules)) {
    Write-Host "[FRONTEND] Installing dependencies (first time setup)..." -ForegroundColor Yellow
    Push-Location $FrontendDir
    & npm install 2>&1 | Out-File (Join-Path $LogDir "npm_install.log")
    Pop-Location
}

$frontendProcess = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c npm run dev -- --host 0.0.0.0 --port 5173" `
    -WorkingDirectory $FrontendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $FrontendLog `
    -RedirectStandardError (Join-Path $LogDir "frontend_error.log") `
    -PassThru

Start-Sleep -Seconds 3

if ($frontendProcess.HasExited) {
    Write-Host "[FRONTEND] Failed to start! Check logs at: $FrontendLog" -ForegroundColor Red
} else {
    Write-Host "[FRONTEND] Started (PID: $($frontendProcess.Id))" -ForegroundColor Green
    Write-Host "           Log: $FrontendLog" -ForegroundColor Gray
}

# Display status
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "       App Started Successfully!       " -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Frontend: http://localhost:5173" -ForegroundColor White
Write-Host "  Backend:  http://localhost:5000" -ForegroundColor White
Write-Host ""
Write-Host "  Press Ctrl+C to stop the app" -ForegroundColor Yellow
Write-Host ""

# Open browser automatically
Start-Process "http://localhost:5173"

# Keep script running and monitor processes
try {
    while ($true) {
        Start-Sleep -Seconds 5
        
        # Check if processes are still running
        $backendRunning = -not $backendProcess.HasExited
        $frontendRunning = -not $frontendProcess.HasExited
        
        if (-not $backendRunning -and -not $frontendRunning) {
            Write-Host "[WARNING] Both processes have stopped!" -ForegroundColor Red
            break
        }
    }
} finally {
    # Cleanup on exit (Ctrl+C)
    Write-Host ""
    Write-Host "[INFO] Stopping app..." -ForegroundColor Yellow
    
    if (-not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
        Write-Host "[BACKEND] Stopped" -ForegroundColor Green
    }
    
    if (-not $frontendProcess.HasExited) {
        Stop-Process -Id $frontendProcess.Id -Force -ErrorAction SilentlyContinue
        # Also kill any npm/node processes on port 5173
        Get-Process -Name "node" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Write-Host "[FRONTEND] Stopped" -ForegroundColor Green
    }
    
    Write-Host ""
    Write-Host "App stopped. Goodbye!" -ForegroundColor Cyan
}
