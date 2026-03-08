#!/bin/bash
# PM2230 Dashboard - Raspberry Pi Build Script
# Run this DIRECTLY on the Raspberry Pi

set -e

echo "======================================================"
echo " PM2230 Dashboard - Raspberry Pi Build Tool"
echo "======================================================"
echo ""

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── 0. Environment Files ───────────────────────────────────────────────────────
echo "[0/4] Checking environment files..."
if [ ! -f "$ROOT_DIR/backend/.env" ]; then
    cp "$ROOT_DIR/backend/.env.example" "$ROOT_DIR/backend/.env"
    echo "  Created backend/.env from example"
fi
if [ ! -f "$ROOT_DIR/frontend/.env.local" ]; then
    cp "$ROOT_DIR/frontend/.env.example" "$ROOT_DIR/frontend/.env.local"
    echo "  Created frontend/.env.local from example"
fi



# ── 1. Python Backend ──────────────────────────────────────────────────────────
echo ""
echo "[1/4] Setting up Python Backend..."
cd "$ROOT_DIR/backend"

if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate

echo "  Installing Python dependencies..."
python3 -m pip install --upgrade pip -q
python3 -m pip install -r requirements.txt -q
python3 -m pip install pyinstaller -q

echo "  Bundling Python backend into executable..."
python3 -m PyInstaller --noconfirm backend-server-linux.spec

cd "$ROOT_DIR"

# ── 2. Frontend ────────────────────────────────────────────────────────────────
echo ""
echo "[2/4] Installing Frontend Dependencies..."
cd "$ROOT_DIR/frontend"
npm install --legacy-peer-deps

echo ""
echo "[3/4] Building Frontend (Static Export)..."
npm run build
cd "$ROOT_DIR"

# ── 3. Assemble Pi Package ──────────────────────────────────────────────────
echo ""
echo "[4/4] Assembling Pi Package..."
rm -rf "$ROOT_DIR/backend/dist/frontend_web"
cp -r "$ROOT_DIR/frontend/out" "$ROOT_DIR/backend/dist/frontend_web"

# Generate the launcher script directly into dist/
cat > "$ROOT_DIR/backend/dist/start-pi.sh" << 'LAUNCHER_EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "======================================================"
echo " PM2230 Dashboard - Pi Launcher"
echo "======================================================"
echo ""
echo " [1] Local only   (this Pi only)"
echo " [2] Tunnel mode  (share public URL)"
echo ""
read -p "Select [1/2]: " MODE

fuser -k 8003/tcp 2>/dev/null || true

if [ ! -f "$SCRIPT_DIR/backend-server" ]; then
    echo "[ERROR] backend-server not found. Run build-pi.sh first."
    exit 1
fi

echo ""
echo "[*] Starting server..."
"$SCRIPT_DIR/backend-server" &
BACKEND_PID=$!

if [ "$MODE" = "2" ]; then
    echo "[*] Waiting for Cloudflare tunnel... (10-20 seconds)"
    TUNNEL_URL=""
    TRIES=0
    while [ $TRIES -lt 30 ]; do
        sleep 2
        TRIES=$((TRIES+1))
        URL=$(curl -s http://localhost:8003/api/v1/tunnel-url 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('url',''))" 2>/dev/null || true)
        if [ ! -z "$URL" ]; then TUNNEL_URL="$URL"; break; fi
    done
    [ -z "$TUNNEL_URL" ] && TUNNEL_URL="http://localhost:8003"
    echo ""
    echo "  Public URL : $TUNNEL_URL"
    echo "  Local URL  : http://localhost:8003"
else
    sleep 3
    echo ""
    echo "  Dashboard: http://localhost:8003"
    echo "  Press Ctrl+C to stop"
    xdg-open "http://localhost:8003" 2>/dev/null || true
fi

trap "kill $BACKEND_PID 2>/dev/null; echo 'Stopped.'" EXIT
wait $BACKEND_PID
LAUNCHER_EOF

chmod +x "$ROOT_DIR/backend/dist/start-pi.sh"
chmod +x "$ROOT_DIR/backend/dist/backend-server"

echo ""
echo "======================================================"
echo " BUILD COMPLETED!"
echo ""
echo "  Package is at: backend/dist/"
echo ""
echo "  To run:   cd backend/dist && ./start-pi.sh"
echo "  Or copy the 'backend/dist/' folder to any Pi and run start-pi.sh"
echo "======================================================"
