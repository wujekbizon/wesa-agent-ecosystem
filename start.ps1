# WESA Launcher — starts daemon + web monitor, opens browser
# Run from: WesaTeam/agent-ecosystem/

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

Write-Host "Starting daemon..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT'; python daemon.py"

Write-Host "Starting web monitor..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT'; python web_monitor.py"

Write-Host "Waiting for web monitor to come up..." -ForegroundColor Yellow
Start-Sleep -Seconds 3

Write-Host "Opening browser..." -ForegroundColor Green
Start-Process "http://localhost:8765/"

Write-Host "All systems go." -ForegroundColor Cyan
