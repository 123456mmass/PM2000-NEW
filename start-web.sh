#!/bin/bash
# PM2230 Dashboard - Linux/Mac Launcher
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "======================================================"
echo " PM2230 Dashboard - Web Mode Launcher"
echo "======================================================"

# Clear port 8003 if in use
fuser -k 8003/tcp 2>/dev/null || true

# Check backend binary
if [ ! -f "$SCRIPT_DIR/backend-server" ]; then
    echo "[!] ไม่พบไฟล์ backend-server"
    echo "    กรุณา build ก่อนด้วย: ./build.sh"
    exit 1
fi

# Start backend (serves API + Frontend)
echo "[*] Starting server..."
"$SCRIPT_DIR/backend-server" &
BACKEND_PID=$!

# Wait and open browser
echo "[*] กำลังเปิด Dashboard..."
sleep 3
xdg-open "http://localhost:8003" 2>/dev/null || \
    open "http://localhost:8003" 2>/dev/null || \
    echo "เปิด Browser แล้วไปที่: http://localhost:8003"

echo ""
echo "======================================================"
echo " ✅ Dashboard: http://localhost:8003"
echo " กด Ctrl+C เพื่อหยุด"
echo "======================================================"

# Wait for Ctrl+C then cleanup
trap "kill $BACKEND_PID 2>/dev/null; echo 'Stopped.'" EXIT
wait $BACKEND_PID
