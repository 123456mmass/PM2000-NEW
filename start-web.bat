@echo off
setlocal enabledelayedexpansion
title PM2230 Dashboard
echo ======================================================
echo  PM2230 Dashboard - Web Mode Launcher
echo ======================================================
echo.
echo  --- Step 1: Engine ---
echo  [1] Python + Rust   (faster, recommended)
echo  [2] Python Only     (no Rust core needed)
echo.
set /p ENGINE="Select Engine [1/2]: "
if "%ENGINE%"=="2" (
    set PM2230_NO_RUST=1
    echo  ^> Engine: Python Only
) else (
    set PM2230_NO_RUST=0
    echo  ^> Engine: Python + Rust
)
echo.
echo  --- Step 2: Network ---
echo  [1] Local only   (faster, this PC only)
echo  [2] Tunnel mode  (share public URL with others)
echo.
set /p MODE="Select Network [1/2]: "
if "%MODE%"=="2" goto TUNNEL_MODE

:: --- LOCAL MODE -----------------------------------------------
:LOCAL_MODE
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8003 2^>nul') do taskkill /f /pid %%a >nul 2>&1

if not exist "%~dp0backend-server.exe" (
    echo [ERROR] backend-server.exe not found
    pause & exit /b 1
)

echo [*] Starting server...
start "" /B "%~dp0backend-server.exe"
echo [*] Opening Dashboard...
timeout /t 3 /nobreak >nul
start "" "http://localhost:8003"

echo.
echo ======================================================
echo  [OK] Dashboard: http://localhost:8003
echo  Press Ctrl+C or close this window to stop
echo ======================================================
pause >nul
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8003 2^>nul') do taskkill /f /pid %%a >nul 2>&1
exit /b

:: --- TUNNEL MODE ----------------------------------------------
:TUNNEL_MODE
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8003 2^>nul') do taskkill /f /pid %%a >nul 2>&1

if not exist "%~dp0backend-server.exe" (
    echo [ERROR] backend-server.exe not found
    pause & exit /b 1
)

echo [*] Starting server + Cloudflare Tunnel...
start "" /B "%~dp0backend-server.exe"
echo [*] Waiting for tunnel... (10-20 seconds)
echo.

set TUNNEL_URL=
set /a TRIES=0
:WAIT_TUNNEL
timeout /t 2 /nobreak >nul
set /a TRIES+=1

for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "$r=(Invoke-RestMethod -Uri 'http://localhost:8003/api/v1/tunnel-url' -UseBasicParsing 2>$null); if($r.url){'READY:'+$r.url}elseif($r.ready){'FAILED'}else{''}" 2^>nul`) do (
    set "RAW=%%U"
)

if "!RAW:~0,6!"=="READY:" (
    set "TUNNEL_URL=!RAW:~6!"
    goto TUNNEL_READY
)
if "!RAW!"=="FAILED" (
    echo [WARN] Tunnel failed - opening local instead
    set TUNNEL_URL=http://localhost:8003
    goto OPEN_BROWSER
)
if !TRIES! GEQ 30 (
    echo [WARN] Timeout - opening local instead
    set TUNNEL_URL=http://localhost:8003
    goto OPEN_BROWSER
)
goto WAIT_TUNNEL

:TUNNEL_READY
echo ======================================================
echo  [OK] Dashboard ready!
echo.
echo  Public URL : !TUNNEL_URL!
echo  Local URL  : http://localhost:8003
echo.
echo  Share the Public URL with others!
echo ======================================================
echo.

:OPEN_BROWSER
start "" "!TUNNEL_URL!"
echo  Press Ctrl+C or close this window to stop
pause >nul
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8003 2^>nul') do taskkill /f /pid %%a >nul 2>&1
