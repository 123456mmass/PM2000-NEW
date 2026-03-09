#!/bin/bash
# Start PM2230 Dashboard (Backend + Frontend)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

echo "Starting PM2230 Dashboard..."
echo ""

echo "Backend: preparing environment"
cd "$BACKEND_DIR"

if [ -d ".venv-mac" ]; then
  VENV_DIR=".venv-mac"
elif [ -d ".venv" ] && [ -f ".venv/bin/activate" ]; then
  VENV_DIR=".venv"
else
  VENV_DIR=".venv-mac"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python3 -m pip install -r requirements.txt -q

pkill -f "python.*main.py" 2>/dev/null || true
python3 main.py &
BACKEND_PID=$!
echo "Backend running (PID: $BACKEND_PID)"

sleep 3

echo "Frontend: starting on port 3002"
cd "$FRONTEND_DIR"
pkill -f "next.*3002" 2>/dev/null || true

# Use webpack mode for cross-platform stability in this project.
npm run dev -- -p 3002 &
FRONTEND_PID=$!
echo "Frontend running (PID: $FRONTEND_PID)"

echo ""
echo "Dashboard: http://localhost:3002"
echo "API:       http://localhost:8002"
echo "API Docs:  http://localhost:8002/docs"
echo ""
echo "Press Ctrl+C to stop all services"

wait
