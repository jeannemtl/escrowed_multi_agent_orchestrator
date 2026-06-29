#!/bin/bash
# ============================================================
# Cerebras Multi-Agent Orchestrator - One Command Startup
# ============================================================
# Usage:
#   ./start.sh           - Start bridge + agents + open dashboard
#   ./start.sh stop      - Kill everything
#   ./start.sh status    - Check what's running
# ============================================================

DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(which python3)"
PORT=8765
MODEL="${MODEL:-gemma4}"       # default to Cerebras Gemma 4
AGENT_COUNT="${AGENT_COUNT:-5}"
BUDGET="${BUDGET:-5.00}"

# Try venv if it exists
if [ -f "$DIR/../venv/bin/python3" ]; then
    PY="$DIR/../venv/bin/python3"
elif [ -f "/tmp/nemoclaw_test/venv/bin/python3" ]; then
    PY="/tmp/nemoclaw_test/venv/bin/python3"
fi

case "$1" in
  stop)
    echo "Stopping orchestrator..."
    lsof -ti:$PORT | xargs kill -9 2>/dev/null
    pkill -f "agent_client.py.*$PORT" 2>/dev/null
    pkill -f "bridge.py.*$PORT" 2>/dev/null
    echo "Done."
    exit 0
    ;;

  status)
    BRIDGE=$(pgrep -f "bridge.py.*$PORT" | head -1)
    AGENTS=$(pgrep -f "agent_client.py.*$PORT" | wc -l | tr -d ' ')
    if [ -n "$BRIDGE" ]; then
      echo "Bridge: RUNNING (PID $BRIDGE) on port $PORT"
    else
      echo "Bridge: NOT RUNNING"
    fi
    echo "Agents: $AGENTS running"
    exit 0
    ;;
esac

# Kill anything on the port
lsof -ti:$PORT | xargs kill -9 2>/dev/null
pkill -f "agent_client.py.*$PORT" 2>/dev/null
sleep 1

echo "============================================"
echo " Cerebras Multi-Agent Orchestrator"
echo "============================================"
echo " Python:  $PY"
echo " Model:   $MODEL (Cerebras Gemma 4 31B)"
echo " Agents:  $AGENT_COUNT"
echo " Budget:  \$$BUDGET"
echo " Port:    $PORT"
echo "============================================"
echo ""

# Start bridge in background
echo "[1/3] Starting bridge..."
cd "$DIR"
"$PY" -u bridge.py --port $PORT --stripe mock --escrow --budget "$BUDGET" \
    > /tmp/orchestrator_bridge.log 2>&1 &
BRIDGE_PID=$!
echo "      PID $BRIDGE_PID, logs: /tmp/orchestrator_bridge.log"

# Wait for bridge to be ready
sleep 2
if ! lsof -ti:$PORT >/dev/null 2>&1; then
    echo "      ERROR: Bridge failed to start. Check /tmp/orchestrator_bridge.log"
    cat /tmp/orchestrator_bridge.log | tail -20
    exit 1
fi
echo "      Bridge is live on port $PORT"

# Start agents in background
echo "[2/3] Starting $AGENT_COUNT agents (model=$MODEL)..."
"$PY" -u agent_client.py --all --count "$AGENT_COUNT" --url ws://localhost:$PORT --model "$MODEL" \
    > /tmp/orchestrator_agents.log 2>&1 &
AGENTS_PID=$!
echo "      PID $AGENTS_PID, logs: /tmp/orchestrator_agents.log"

sleep 2
echo "      Agents connected"

# Open dashboard
echo "[3/3] Opening dashboard..."
open "$DIR/dashboard.html"

echo ""
echo "============================================"
echo " READY!"
echo "============================================"
echo ""
echo " Dashboard:  opened in browser"
echo " Bridge log: /tmp/orchestrator_bridge.log"
echo " Agents log: /tmp/orchestrator_agents.log"
echo ""
echo " To stop:    ./start.sh stop"
echo " To check:   ./start.sh status"
echo "============================================"
