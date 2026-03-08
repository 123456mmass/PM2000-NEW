@echo off
setlocal enabledelayedexpansion
echo ======================================================
echo PM2230 Dashboard - Windows Build Automation Tool
echo ======================================================
echo.

:: Clear potentially colliding ports
echo [*] Cleaning up ports 8003 and 3000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8003') do taskkill /f /pid %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :3000') do taskkill /f /pid %%a >nul 2>&1

:: 0. Setup Environment Files
echo [0/3] Checking environment files...
if not exist backend\.env (
    echo Creating backend\.env from example...
    copy backend\.env.example backend\.env
)
if not exist frontend\.env.local (
    echo Creating frontend\.env.local from example...
    copy frontend\.env.example frontend\.env.local
)



:: 1. Setup Backend
echo [1/3] Setting up Python Backend...
cd backend
if not exist .venv (
    echo Creating virtual environment...
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment. Is Python 3.12 installed?
        echo Download from: https://www.python.org/downloads/release/python-3121/
        pause
        exit /b 1
    )
)
call .venv\Scripts\activate.bat
echo Installing Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo Bundling Python backend into executable (Sidecar)...
python -m PyInstaller --noconfirm backend-server.spec
cd ..

:: 2. Setup Frontend
echo.
echo [2/3] Setting up Frontend Dependencies...
cd frontend
echo Installing Node.js packages (this may take a while)...
call npm install --legacy-peer-deps

:: 3. Build Static Frontend
echo.
echo [3/3] Building Frontend (Next.js Export)...
call npm run build
cd ..

:: 4. Copy frontend into backend/dist for Web Mode (AFTER frontend is built!)
echo.
echo [+] Preparing Web Mode package...
if exist backend\dist\frontend_web (rmdir /s /q backend\dist\frontend_web)
xcopy /E /I /Q frontend\out backend\dist\frontend_web >nul
copy start-web.bat backend\dist\ >nul

echo.
echo ======================================================
echo BUILD COMPLETED!
echo.
echo   Web Package  : backend\dist\    (ZIP to share)
echo ======================================================
pause
