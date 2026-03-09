#!/bin/bash

echo "======================================================"
echo "PM2230 Dashboard - Linux Build Automation Tool"
echo "======================================================"
echo ""

# Clear potentially colliding ports
echo "[*] Cleaning up ports 8003 and 3000..."
sudo fuser -k 8003/tcp 3000/tcp || true

# 0. Setup Environment Files
echo "[0/3] Checking environment files..."
if [ ! -f backend/.env ]; then
    echo "Creating backend/.env from example..."
    cp backend/.env.example backend/.env
fi



if [ ! -f frontend/.env.local ]; then
    echo "Creating frontend/.env.local from example..."
    cp frontend/.env.example frontend/.env.local
fi

# 1. Setup Backend & Build Sidecar
echo "[1/3] Setting up Python Backend..."
cd backend
if [ ! -d .venv ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
echo "Installing Python dependencies..."
python3 -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

echo "Bundling Python backend into executable (Sidecar)..."
pyinstaller --noconfirm --onefile --console --name "backend-server" main.py
cp .env dist/ 2>/dev/null || true
cd ..

# 2. Setup Frontend
echo ""
echo "[2/3] Setting up Frontend Dependencies..."
cd frontend
echo "Installing Node.js packages..."
npm install --legacy-peer-deps

# 3. Build Static Frontend
echo ""
echo "[3/3] Building Frontend (Next.js Export)..."
npm run build
cd ..

# 4. Copy frontend into backend/dist for Web Mode (AFTER frontend is built!)
echo ""
echo "[+] Preparing Web Mode package..."
rm -rf backend/dist/frontend_web
cp -r frontend/out backend/dist/frontend_web
cp start-web.bat backend/dist/ 2>/dev/null || true
cp start-web.sh backend/dist/ 2>/dev/null || true
chmod +x backend/dist/start-web.sh 2>/dev/null || true
chmod +x backend/dist/backend-server 2>/dev/null || true

echo ""
echo "======================================================"
echo "BUILD COMPLETED!"
echo ""
echo "Web Package  : backend/dist/  (ZIP to share)"
echo "======================================================"
read -p "Press [Enter] to exit..."


