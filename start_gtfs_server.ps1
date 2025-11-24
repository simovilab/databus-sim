# Start GTFS WebSocket Server
Write-Host "Starting GTFS WebSocket Server..." -ForegroundColor Green
Write-Host ""
Write-Host "Server will run on ws://localhost:8765" -ForegroundColor Cyan
Write-Host "Dashboard: file:///$PWD/dashboard/dashboard.html" -ForegroundColor Cyan
Write-Host ""
Write-Host "Open dashboard/dashboard.html in your web browser" -ForegroundColor Yellow
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Yellow
Write-Host ""

python gtfs_websocket_server.py
