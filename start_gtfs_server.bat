@echo off
echo Starting GTFS WebSocket Server...
echo.
echo Server will run on ws://localhost:8765
echo Dashboard: file:///%CD%/dashboard/dashboard.html
echo.
echo Press Ctrl+C to stop the server
echo.
python gtfs_websocket_server.py
