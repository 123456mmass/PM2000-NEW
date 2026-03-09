@echo off
setlocal enabledelayedexpansion
title PM2230 Dashboard
echo ======================================================
echo  PM2230 Dashboard - Windows Launcher
echo ======================================================
echo.

:: Clear port 8003 if in use
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8003 2^>nul') do taskkill /f /pid %%a >nul 2>&1

:: Check backend exe
if not exist "%~dp0backend\dist\backend-server.exe" (
    if not exist "%~dp0backend-server.exe" (
        echo [!] ไม่พบไฟล์ backend-server.exe
        echo     กรุณารัน build-windows.bat ก่อน
        pause
        exit /b 1
    )
    set "EXE=%~dp0backend-server.exe"
) else (
    set "EXE=%~dp0backend\dist\backend-server.exe"
)

:: Start backend (serves API + Frontend)
echo [*] Starting server...
start "" /B "%EXE%"

:: Wait and open browser
echo [*] กำลังเปิด Dashboard...
timeout /t 3 /nobreak > nul
start "" "http://localhost:8003"

echo.
echo ======================================================
echo  ✅ Dashboard: http://localhost:8003
echo  กด Ctrl+C หรือปิดหน้าต่างนี้เพื่อหยุด
echo ======================================================
pause > nul

:: Cleanup
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8003 2^>nul') do taskkill /f /pid %%a >nul 2>&1
