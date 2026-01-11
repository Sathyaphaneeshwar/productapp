# Stop App Script for Windows
# Stops all backend and frontend processes

$Host.UI.RawUI.WindowTitle = "Stopping Stock App"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "       Stopping Stock App              " -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Stop Python/Flask processes
Write-Host "[BACKEND] Stopping Python processes..." -ForegroundColor Yellow
Get-Process -Name "python*" -ErrorAction SilentlyContinue | ForEach-Object {
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId = $($_.Id)" -ErrorAction SilentlyContinue).CommandLine
    if ($cmdline -like "*app.py*") {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  Stopped PID: $($_.Id)" -ForegroundColor Gray
    }
}

# Stop Node/npm processes
Write-Host "[FRONTEND] Stopping Node processes..." -ForegroundColor Yellow
Get-Process -Name "node" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# Kill anything on ports 5001 and 5173
Write-Host "[PORTS] Freeing ports 5001 and 5173..." -ForegroundColor Yellow
$connections = Get-NetTCPConnection -LocalPort 5001,5173 -ErrorAction SilentlyContinue
foreach ($conn in $connections) {
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "[SUCCESS] App stopped!" -ForegroundColor Green
Write-Host ""
