# Cerebrain

Multi-agent orchestration with on-chain attestation and Stripe escrow, running 37x faster on Cerebras wafer-scale inference.

WarpDAG decomposes prompts into layered task DAGs, dispatches them to AI agents, verifies results, attests batches on Solana, and releases escrow payments to agent wallets. The same pipeline runs simultaneously on Cerebras and a traditional GPU provider, with a live dashboard showing a side-by-side race.

## Results

| Metric | Cerebras Gemma 4 31B | GPU GLM-5.2 (OpenRouter) |
|--------|----------------------|---------------------------|
| Task 1 | 1.1s | 49.9s |
| Task 2 | 1.5s | 43.2s |
| Task 3 | 1.3s | 51.2s |
| **Total** | **3.9s** | **144.4s** |
| **Speedup** | **37.1x** | |

## Requirements

- Python 3.9+
- pip packages: `websockets`, `aiohttp`
- API keys (see below)

## Setup

### 1. Clone

```bash
git clone https://github.com/jeannemtl/cerebras_multi_agent_orchestrator.git
cd cerebras_multi_agent_orchestrator
```

### 2. Install dependencies

```bash
pip install websockets aiohttp
```

### 3. API keys

The code reads from `../.env` or `~/.hermes/.env`. Create one:

```bash
cat > ../.env << 'EOF'
CEREBRAS_API_KEY=your_cerebras_key
OPENROUTER_API_KEY=your_openrouter_key
NVIDIA_API_KEY=your_nvidia_key
EOF
```

| Key | Where to get it | Used for |
|-----|----------------|----------|
| `CEREBRAS_API_KEY` | https://cloud.cerebras.ai | Cerebras Gemma 4 31B (fast side) |
| `OPENROUTER_API_KEY` | https://openrouter.ai | GPU GLM-5.2 (slow side) |
| `NVIDIA_API_KEY` | https://build.nvidia.com | Nemotron planner (prompt decomposition) |

Or export as environment variables:

```bash
export CEREBRAS_API_KEY=your_key
export OPENROUTER_API_KEY=your_key
export NVIDIA_API_KEY=your_key
```

## Run the Race

### One-command startup

```bash
./start.sh
```

This does three things:
1. Starts the bridge (WebSocket server on port 8765 with mock Stripe + escrow)
2. Starts 5 agent clients (Cerebras Gemma 4 31B)
3. Opens the dashboard in your browser

### Run the race

1. In the dashboard, click **Connect**
2. Type a prompt (or leave empty for default Cerebras-themed tasks)
3. Click **Race**
4. Watch both sides run simultaneously:
   - **Cerebras** (left, green) finishes in ~4 seconds
   - **GPU** (right, red) takes ~144 seconds
5. The speedup badge appears in the header showing the result

### What you see during the race

- **Decomposition bar** - how the prompt splits into batches
- **Progress steppers** - 4 steps per side: Attest Batch 0, Attest Batch 1, Confirmation, Escrow Payment
- **Task cards** - pending to running to done, with latency and output preview
- **Attestation chips** - Solana signatures under each batch
- **Escrow bars** - filling as payments release to agent wallets
- **Finish banner** - final speedup number

## Other commands

```bash
./start.sh stop       # Kill bridge + agents
./start.sh status     # Check if running
```

## Architecture

```
Prompt
  |
  v
Planner (Nemotron 3 Ultra)
  |
  v
Task DAG (batches with dependencies)
  |
  v
+------------------+     +------------------+
| Cerebras Agents   |     | GPU Agents       |
| Gemma 4 31B       |     | GLM-5.2          |
+------------------+     +------------------+
  |                        |
  v                        v
Verifier (checks output)
  |
  v
Attestor (Solana on-chain proof)
  |
  v
Escrow (Stripe payment to agent wallets)
```

### Components

| File | Role |
|------|------|
| `bridge.py` | WebSocket server, orchestrates the full pipeline |
| `agent_client.py` | Agent worker, calls Cerebras or GPU models |
| `planner.py` | Decomposes prompts into task DAGs via Nemotron |
| `task_dag.py` | DAG builder with dependency tracking |
| `verifier.py` | Validates agent outputs |
| `solana_attestor.py` | Mock/devnet Solana attestation |
| `stripe_charger.py` | Mock/test/live Stripe payment handling |
| `dashboard.html` | Real-time dashboard with race mode |
| `benchmark_speed.py` | Standalone benchmark script |
| `start.sh` | One-command startup |

### Stripe modes

| Mode | What it does |
|------|-------------|
| `mock` (default) | No real Stripe API, simulated payments |
| `test` | Real Stripe API with test card 4242, no real charges |
| `live` | Real Stripe charges (requires STRIPE_API_KEY) |

### Solana attestation

- **Default:** Mock mode (simulated signatures, no on-chain txs)
- **Devnet:** Pass `--solana` flag to the bridge for real Solana devnet attestation

## Full pipeline (without race)

To run the full orchestrator pipeline (plan, dispatch, verify, attest, escrow):

1. Click **Connect** in the dashboard
2. Type a prompt
3. Click **Plan & Submit** (Nemotron decomposes it into tasks)
4. Click **Start Pipeline** (agents execute, verify, attest, escrow releases)

## License

Apache 2.0
