#!/bin/bash
# Multi-Agent Orchestrator runner
# Usage: ./run.sh [bridge|solana|agents|agent|e2e|ws-test|dashboard|escrow|budget-test]
#
# Escrow modes:
#   bridge       — mock Solana + mock Stripe (no real anything)
#   escrow       — mock Solana + Stripe test mode (real Stripe API, test card 4242)
#   solana       — real Solana devnet + mock Stripe

PY=~/.hermes/hermes-agent/venv/bin/python
DIR=~/multi-agent-orchestrator

case "$1" in
  bridge)
    echo "Starting bridge (mock Solana, mock Stripe) on port 8765..."
    lsof -ti:8765 | xargs kill -9 2>/dev/null; sleep 0.5
    cd "$DIR" && "$PY" bridge.py --port 8765 --stripe mock --budget 5.00
    ;;
  escrow)
    echo "Starting bridge (mock Solana, Stripe TEST mode) on port 8765..."
    echo "Requires STRIPE_API_KEY env var (sk_test_...)"
    lsof -ti:8765 | xargs kill -9 2>/dev/null; sleep 0.5
    cd "$DIR" && "$PY" bridge.py --port 8765 --stripe test --budget 5.00
    ;;
  solana)
    echo "Starting bridge (real Solana devnet, mock Stripe) on port 8765..."
    cd "$DIR" && "$PY" bridge.py --port 8765 --solana --stripe mock --budget 5.00
    ;;
  full-escrow)
    echo "Starting bridge (real Solana devnet, Stripe TEST mode) on port 8765..."
    cd "$DIR" && "$PY" bridge.py --port 8765 --solana --stripe test --budget 5.00
    ;;
  budget-test)
    echo "Starting bridge with $2 budget cap..."
    cd "$DIR" && "$PY" bridge.py --port 8765 --stripe mock --budget "$2"
    ;;
  agents)
    echo "Starting all 10 agent clients..."
    cd "$DIR" && "$PY" agent_client.py --all --url ws://localhost:8765
    ;;
  agent)
    echo "Starting single agent: $2"
    cd "$DIR" && "$PY" agent_client.py --agent "$2" --url ws://localhost:8765
    ;;
  e2e)
    echo "Running E2E test..."
    cd "$DIR" && "$PY" e2e_test.py
    ;;
  ws-test)
    echo "Running WebSocket integration test..."
    cd "$DIR" && "$PY" ws_integration_test.py
    ;;
  escrow-test)
    echo "Running escrow + budget enforcement test..."
    cd "$DIR" && "$PY" escrow_test.py
    ;;
  dashboard)
    echo "Opening dashboard..."
    open "$DIR/dashboard.html"
    ;;
  *)
    echo "Multi-Agent Orchestrator"
    echo "Usage: ./run.sh [bridge|escrow|solana|agents|agent <id>|e2e|ws-test|escrow-test|budget-test <dollars>|dashboard]"
    echo ""
    echo "  bridge         - Mock Solana + mock Stripe (no real anything)"
    echo "  escrow         - Mock Solana + Stripe TEST mode (real API, test card)"
    echo "  solana         - Real Solana devnet + mock Stripe"
    echo "  full-escrow    - Real Solana devnet + Stripe TEST mode"
    echo "  agents         - Start all 10 agent clients"
    echo "  agent <id>    - Start single agent (e.g. agent-1)"
    echo "  e2e            - Run end-to-end test"
    echo "  ws-test        - Run WebSocket integration test"
    echo "  escrow-test    - Run escrow + budget enforcement test"
    echo "  budget-test $  - Start with custom budget cap (e.g. 0.50)"
    echo "  dashboard      - Open dashboard in browser"
    echo ""
    echo "Typical workflow:"
    echo "  Terminal 1: ./run.sh bridge"
    echo "  Terminal 2: ./run.sh agents    (after loading tasks in dashboard)"
    echo "  Terminal 3: ./run.sh dashboard"
    echo ""
    echo "Escrow workflow (Stripe test mode):"
    echo "  Terminal 1: ./run.sh escrow     (needs STRIPE_API_KEY env var)"
    echo "  Terminal 2: ./run.sh agents"
    echo "  Terminal 3: ./run.sh dashboard"
esac
