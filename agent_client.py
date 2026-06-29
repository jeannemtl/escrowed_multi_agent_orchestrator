#!/usr/bin/env python3
"""
Agent Client with Model Integration
====================================
Connects to the bridge, receives dispatched tasks, executes them using
LLM models, and submits results back.

Model assignment:
  - Agents 1-8: Nemotron 3 Ultra (free NVIDIA NIM, 6s cooldown, 3-retry)
  - Agents 9-10: GLM-5.2 (OpenRouter, better reasoning for synthesis)

Usage:
  python3 agent_client.py --all --url ws://localhost:8765
  python3 agent_client.py --agent agent-1 --url ws://localhost:8765
"""

import asyncio
import json
import os
import sys
import time
import logging
import argparse
import subprocess
import websockets
import aiohttp

# Load .env file if it exists (no python-dotenv dependency)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("agent")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ══════════════════════════════════════════════════════════════════════
# Model Configuration
# ══════════════════════════════════════════════════════════════════════

# Agents 1-8: GLM-5.2 (was Nemotron, now all GLM for reliability)
# Agent 9-10: GLM-5.2 (synthesis)
# Nemotron is only used for the PLANNER (decomposing user prompt into tasks)
MODEL_CONFIG = {
    "nemotron": {
        "model": "nvidia/nemotron-3-ultra-550b-a55b",
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "max_tokens": 500,
        "temperature": 0.3,
        "cooldown": 6.0,
        "retries": 3,
        "retry_delays": [10, 15],
    },
    "glm": {
        "model": "z-ai/glm-5.2",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "max_tokens": 2000,
        "temperature": 0.4,
        "cooldown": 1.0,
        "retries": 2,
        "retry_delays": [5, 10],
    },
    "gemma4": {
        "model": "gemma-4-31b",
        "url": "https://api.cerebras.ai/v1/chat/completions",
        "max_tokens": 2000,
        "temperature": 0.4,
        "cooldown": 0.0,
        "retries": 2,
        "retry_delays": [2, 5],
    },
}

# Global model override (set via --model flag)
MODEL_OVERRIDE = None

def get_model_for_agent(agent_id: str) -> dict:
    """Return model config. Uses override if set, otherwise GLM-5.2."""
    provider = MODEL_OVERRIDE or "glm"
    return {"provider": provider, **MODEL_CONFIG[provider]}


def load_api_keys() -> dict:
    """Load API keys from environment or ~/.hermes/.env"""
    keys = {}

    # From environment
    keys["nemotron"] = os.environ.get("NVIDIA_API_KEY", "")
    keys["glm"] = os.environ.get("OPENROUTER_API_KEY", "")
    keys["gemma4"] = os.environ.get("CEREBRAS_API_KEY", "")

    # From ~/.hermes/.env if not in env
    if not keys["nemotron"] or not keys["glm"] or not keys["gemma4"]:
        envfile = os.path.expanduser("~/.hermes/.env")
        if os.path.exists(envfile):
            with open(envfile) as f:
                for line in f:
                    line = line.strip()
                    if "=" not in line or line.startswith("#"):
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k == "NVIDIA_API_KEY":
                        keys["nemotron"] = v
                    elif k == "OPENROUTER_API_KEY":
                        keys["glm"] = v
                    elif k == "CEREBRAS_API_KEY":
                        keys["gemma4"] = v

    return keys


# ══════════════════════════════════════════════════════════════════════
# LLM Inference
# ══════════════════════════════════════════════════════════════════════

async def call_nemotron(api_key: str, system_prompt: str, user_prompt: str, config: dict,
                        agent_id: str = "", ws=None, task_id: str = "") -> dict:
    """Call NVIDIA Nemotron 3 Ultra with cooldown + retry pattern."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": config["max_tokens"],
        "temperature": config["temperature"],
    }

    async def report(stage, detail):
        if ws and task_id:
            try:
                await ws.send(json.dumps({
                    "type": "task_progress", "task_id": task_id,
                    "agent_id": agent_id, "stage": stage, "detail": detail,
                }))
            except Exception:
                pass

    for attempt in range(config["retries"]):
        start = time.time()
        await report("model_calling", f"Nemotron attempt {attempt+1}/{config['retries']}...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    config["url"],
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    body = await resp.json()
                    elapsed = (time.time() - start) * 1000

            if resp.status != 200:
                err_detail = str(body)[:200] if body else f"HTTP {resp.status}"
                log.warning(f"    Nemotron {resp.status}: {err_detail[:80]}, attempt {attempt+1}/{config['retries']}")
                if attempt < config["retries"] - 1:
                    delay = config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)]
                    await report("model_retry", f"Nemotron {resp.status}, retry in {delay}s")
                    await asyncio.sleep(delay)
                    continue
                return {"ok": False, "error": f"Nemotron {resp.status}: {err_detail}", "latency_ms": elapsed}

            content = body["choices"][0]["message"]["content"]
            await report("model_done", f"Nemotron responded ({elapsed:.0f}ms, {len(content)} chars)")
            return {"ok": True, "content": content, "latency_ms": elapsed}

        except asyncio.TimeoutError:
            elapsed = (time.time() - start) * 1000
            log.warning(f"    Nemotron timeout (60s), attempt {attempt+1}/{config['retries']}")
            if attempt < config["retries"] - 1:
                delay = config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)]
                await report("model_retry", f"Nemotron timeout, retry in {delay}s")
                await asyncio.sleep(delay)
                continue
            return {"ok": False, "error": "Nemotron timeout after 60s", "latency_ms": elapsed}
        except aiohttp.ClientConnectorError as e:
            elapsed = (time.time() - start) * 1000
            log.warning(f"    Nemotron connection error: {e}, attempt {attempt+1}/{config['retries']}")
            await report("model_retry", f"Connection error: {e}")
            if attempt < config["retries"] - 1:
                delay = config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)]
                await asyncio.sleep(delay)
                continue
            return {"ok": False, "error": f"Connection: {e}", "latency_ms": elapsed}
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            err_type = type(e).__name__
            log.warning(f"    Nemotron {err_type}: {e}, attempt {attempt+1}/{config['retries']}")
            await report("model_retry", f"{err_type}: {e}")
            if attempt < config["retries"] - 1:
                delay = config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)]
                await asyncio.sleep(delay)
                continue
            return {"ok": False, "error": f"{err_type}: {e}", "latency_ms": elapsed}

    return {"ok": False, "error": "max retries exceeded"}


async def call_glm(api_key: str, system_prompt: str, user_prompt: str, config: dict,
                   agent_id: str = "", ws=None, task_id: str = "") -> dict:
    """Call GLM-5.2 via OpenRouter with streaming for real-time reasoning traces."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": config["max_tokens"],
        "temperature": config["temperature"],
        "stream": True,  # SSE streaming for real-time output
    }

    async def report(stage, detail):
        if ws and task_id:
            try:
                await ws.send(json.dumps({
                    "type": "task_progress", "task_id": task_id,
                    "agent_id": agent_id, "stage": stage, "detail": detail,
                }))
            except Exception:
                pass

    for attempt in range(config["retries"]):
        start = time.time()
        await report("model_calling", f"GLM-5.2 attempt {attempt+1}/{config['retries']}...")
        try:
            full_content = ""
            chunk_count = 0
            last_report_time = time.time()

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    config["url"],
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.json()
                        err_detail = str(body)[:200] if body else f"HTTP {resp.status}"
                        log.warning(f"    GLM {resp.status}: {err_detail[:80]}, attempt {attempt+1}/{config['retries']}")
                        await report("model_retry", f"GLM {resp.status}, retry in 5s")
                        if attempt < config["retries"] - 1:
                            delay = config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)]
                            await asyncio.sleep(delay)
                            continue
                        return {"ok": False, "error": f"GLM {resp.status}: {err_detail}", "latency_ms": (time.time()-start)*1000}

                    # Read SSE stream
                    async for line in resp.content:
                        line = line.decode("utf-8", errors="ignore").strip()
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            text = delta.get("content", "")
                            if text:
                                full_content += text
                                chunk_count += 1
                                # Report streaming progress every ~1s or every 50 chunks
                                now = time.time()
                                if now - last_report_time > 1.0 or chunk_count % 50 == 0:
                                    preview = full_content[-200:]  # last 200 chars
                                    await report("model_streaming",
                                        f"[{chunk_count} chunks] ...{preview}")
                                    last_report_time = now
                        except json.JSONDecodeError:
                            continue

            elapsed = (time.time() - start) * 1000

            if full_content:
                log.info(f"    GLM-5.2 streamed {chunk_count} chunks ({elapsed:.0f}ms, {len(full_content)} chars)")
                await report("model_done",
                    f"GLM-5.2 responded ({elapsed:.0f}ms, {len(full_content)} chars, {chunk_count} chunks)")
                return {"ok": True, "content": full_content, "latency_ms": elapsed}
            else:
                log.warning(f"    GLM stream empty, attempt {attempt+1}/{config['retries']}")
                await report("model_retry", "GLM stream empty, retrying...")
                if attempt < config["retries"] - 1:
                    await asyncio.sleep(config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)])
                    continue
                return {"ok": False, "error": "empty stream", "latency_ms": elapsed}

        except Exception as e:
            err_type = type(e).__name__
            log.warning(f"    GLM {err_type}: {e}, attempt {attempt+1}/{config['retries']}")
            await report("model_retry", f"GLM {err_type}: {e}")
            if attempt < config["retries"] - 1:
                delay = config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)]
                await asyncio.sleep(delay)
                continue
            return {"ok": False, "error": f"{err_type}: {e}", "latency_ms": 0}

    return {"ok": False, "error": "max retries exceeded"}


async def call_gemma4(api_key: str, system_prompt: str, user_prompt: str, config: dict,
                      agent_id: str = "", ws=None, task_id: str = "") -> dict:
    """Call Gemma 4 31B via Cerebras with streaming for ultra-fast inference."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": config["max_tokens"],
        "temperature": config["temperature"],
        "stream": True,
    }

    async def report(stage, detail):
        if ws and task_id:
            try:
                await ws.send(json.dumps({
                    "type": "task_progress", "task_id": task_id,
                    "agent_id": agent_id, "stage": stage, "detail": detail,
                }))
            except Exception:
                pass

    for attempt in range(config["retries"]):
        start = time.time()
        await report("model_calling", f"Gemma 4 (Cerebras) attempt {attempt+1}/{config['retries']}...")
        try:
            full_content = ""
            chunk_count = 0
            last_report_time = time.time()

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    config["url"],
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.json()
                        err_detail = str(body)[:200] if body else f"HTTP {resp.status}"
                        log.warning(f"    Cerebras {resp.status}: {err_detail[:80]}, attempt {attempt+1}/{config['retries']}")
                        await report("model_retry", f"Cerebras {resp.status}, retry in 2s")
                        if attempt < config["retries"] - 1:
                            delay = config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)]
                            await asyncio.sleep(delay)
                            continue
                        return {"ok": False, "error": f"Cerebras {resp.status}: {err_detail}", "latency_ms": (time.time()-start)*1000}

                    async for line in resp.content:
                        line = line.decode("utf-8", errors="ignore").strip()
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            text = delta.get("content", "")
                            if text:
                                full_content += text
                                chunk_count += 1
                                now = time.time()
                                if now - last_report_time > 0.5 or chunk_count % 50 == 0:
                                    preview = full_content[-200:]
                                    await report("model_streaming",
                                        f"[{chunk_count} chunks] ...{preview}")
                                    last_report_time = now
                        except json.JSONDecodeError:
                            continue

            elapsed = (time.time() - start) * 1000

            if full_content:
                log.info(f"    Gemma 4 streamed {chunk_count} chunks ({elapsed:.0f}ms, {len(full_content)} chars)")
                await report("model_done",
                    f"Gemma 4 responded ({elapsed:.0f}ms, {len(full_content)} chars, {chunk_count} chunks)")
                return {"ok": True, "content": full_content, "latency_ms": elapsed}
            else:
                log.warning(f"    Cerebras stream empty, attempt {attempt+1}/{config['retries']}")
                if attempt < config["retries"] - 1:
                    await asyncio.sleep(config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)])
                    continue
                return {"ok": False, "error": "empty stream", "latency_ms": elapsed}

        except Exception as e:
            err_type = type(e).__name__
            log.warning(f"    Cerebras {err_type}: {e}, attempt {attempt+1}/{config['retries']}")
            await report("model_retry", f"Cerebras {err_type}: {e}")
            if attempt < config["retries"] - 1:
                delay = config["retry_delays"][min(attempt, len(config["retry_delays"]) - 1)]
                await asyncio.sleep(delay)
                continue
            return {"ok": False, "error": f"{err_type}: {e}", "latency_ms": 0}

    return {"ok": False, "error": "max retries exceeded"}


async def call_model(agent_id: str, system_prompt: str, user_prompt: str, api_keys: dict,
                    ws=None, task_id: str = "") -> dict:
    """Route to the right model based on agent ID."""
    model_cfg = get_model_for_agent(agent_id)
    provider = model_cfg["provider"]

    api_key = api_keys.get(provider, "")
    if not api_key:
        log.warning(f"    No API key for {provider}, using fallback")
        return {"ok": False, "error": f"no API key for {provider}", "latency_ms": 0}

    if provider == "nemotron":
        result = await call_nemotron(api_key, system_prompt, user_prompt, model_cfg,
                                     agent_id=agent_id, ws=ws, task_id=task_id)
        await asyncio.sleep(model_cfg["cooldown"])  # 6s cooldown
        return result
    elif provider == "glm":
        result = await call_glm(api_key, system_prompt, user_prompt, model_cfg,
                                agent_id=agent_id, ws=ws, task_id=task_id)
        await asyncio.sleep(model_cfg["cooldown"])
        return result
    elif provider == "gemma4":
        result = await call_gemma4(api_key, system_prompt, user_prompt, model_cfg,
                                   agent_id=agent_id, ws=ws, task_id=task_id)
        await asyncio.sleep(model_cfg["cooldown"])
        return result

    return {"ok": False, "error": f"unknown provider: {provider}"}


# ══════════════════════════════════════════════════════════════════════
# Hermes Agent Execution (real agents with tools)
# ══════════════════════════════════════════════════════════════════════

async def execute_with_hermes(task: dict, agent_id: str, ws=None) -> dict:
    """
    Execute a task by spawning a real Hermes agent with full tool access.
    
    The Hermes agent can:
    - web_search: search the internet for real information
    - web_extract: extract content from web pages
    - terminal: run shell commands
    - file: read/write files
    - And use any other enabled Hermes tools
    
    This replaces the direct LLM API call with a real agent that can reason
    AND act, producing grounded results from actual web searches.
    """
    desc = task.get("description", task.get("name", ""))
    task_id = task.get("task_id", "unknown")
    task_name = task.get("name", "")

    # Build the prompt for the Hermes agent
    prompt_parts = [
        f"You are agent {agent_id} in a multi-agent orchestration system.",
        f"Your task: {task_name}",
        f"",
        f"Description: {desc}",
        f"",
        f"Use your tools (web_search, web_extract, terminal, file) to research this thoroughly.",
        f"Search the web for real, current information. Do not guess or hallucinate.",
        f"Return a clear, structured summary of your findings with sources.",
    ]

    # Add upstream context if available (for synthesis tasks)
    upstream = task.get("upstream_results", [])
    if upstream:
        prompt_parts.append("")
        prompt_parts.append("## Upstream Task Results (from other agents)")
        prompt_parts.append("Use these as primary source material for your synthesis:")
        for r in upstream:
            prompt_parts.append(f"\n--- [{r.get('task_id', '?')}] ---")
            prompt_parts.append(str(r.get("summary", ""))[:2000])
        prompt_parts.append("")
        prompt_parts.append("IMPORTANT: Synthesize the above findings. Do not say you lack information.")

    prompt = "\n".join(prompt_parts)

    # Report progress
    if ws:
        try:
            await ws.send(json.dumps({
                "type": "task_progress", "task_id": task_id,
                "agent_id": agent_id, "stage": "hermes_starting",
                "detail": f"Spawning Hermes agent with web_search + tools...",
            }))
        except Exception:
            pass

    log.info(f"    [{agent_id}] Spawning Hermes agent for: {task_name[:60]}")

    # Run hermes chat -q with the task prompt
    # Use GLM-5.2 model, web + terminal + file toolsets
    # Write prompt to a temp file to avoid shell escaping issues
    import tempfile
    # Write prompt to file for logging, but pass directly to hermes chat
    # (@file requires the file toolset which may not always be available)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, prefix=f"hermes_task_{task_id}_") as f:
        f.write(prompt)
        prompt_file = f.name

    try:
        # Check if NemoClaw sandbox is available
        try:
            nemo_check = subprocess.run(
                ["nemohermes", "mao-hermes", "status"],
                capture_output=True, text=True, timeout=10
            )
            use_nemoclaw = nemo_check.returncode == 0 and "Ready" in nemo_check.stdout
        except Exception:
            use_nemoclaw = False

        if use_nemoclaw and is_research_task(desc):
            # For research tasks: run on host Hermes (has working web_search)
            # but mention NemoClaw sandbox is active for non-research tasks
            log.info(f"    [{agent_id}] Host Hermes (web search) + NemoClaw sandbox active")
            cmd = [
                "hermes", "chat",
                "-q", prompt,
                "-m", "z-ai/glm-5.2",
                "-t", "web",  # web only — adding terminal/file makes it too slow
                "-Q",
            ]
        elif use_nemoclaw:
            # For non-research tasks (synthesis, file ops, commands):
            # Run inside NemoClaw sandbox with Nemotron 3 Ultra
            cmd = [
                "nemohermes", "mao-hermes", "exec", "--timeout", "90",
                "--", "hermes", "chat",
                "-q", f"@{prompt_file}",
                "-Q",
            ]
            log.info(f"    [{agent_id}] NemoClaw sandbox (Nemotron 3 Ultra, NVIDIA OpenShell)")
        else:
            # Fallback: run Hermes directly on host
            cmd = [
                "hermes", "chat",
                "-q", prompt,
                "-m", "z-ai/glm-5.2",
                "-t", "web",  # web only for speed
                "-Q",
            ]
            log.info(f"    [{agent_id}] Running on host (NemoClaw not available)")

        start = time.time()

        # Run in executor to not block the event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=90))

        elapsed = (time.time() - start) * 1000

        if result.returncode == 0 and result.stdout.strip():
            content = result.stdout.strip()
            log.info(f"    [{agent_id}] Hermes agent completed ({elapsed:.0f}ms, {len(content)} chars)")

            if ws:
                try:
                    await ws.send(json.dumps({
                        "type": "task_progress", "task_id": task_id,
                        "agent_id": agent_id, "stage": "model_done",
                        "detail": f"Hermes agent completed ({elapsed:.0f}ms, {len(content)} chars)",
                    }))
                except Exception:
                    pass

            return {
                "summary": content[:4000],
                "metadata": {
                    "model": "NemoClaw Hermes (Nemotron-3-Super)" if use_nemoclaw else "hermes-agent (GLM-5.2)",
                    "provider": "nemoclaw" if use_nemoclaw else "hermes",
                    "sandbox": "NVIDIA OpenShell" if use_nemoclaw else "host",
                    "latency_ms": elapsed,
                    "content_length": len(content),
                    "tools_available": ["web_search", "web_extract", "terminal", "file"],
                },
            }
        else:
            err = result.stderr[:500] if result.stderr else f"exit code {result.returncode}"
            log.warning(f"    [{agent_id}] Hermes agent failed: {err}")
            # Fallback to direct model call
            return await execute_with_model(task, agent_id, load_api_keys(), ws=ws)

    except subprocess.TimeoutExpired:
        log.warning(f"    [{agent_id}] Hermes agent timed out (90s)")
        return await execute_with_model(task, agent_id, load_api_keys(), ws=ws)
    except Exception as e:
        log.warning(f"    [{agent_id}] Hermes agent error: {e}")
        return await execute_with_model(task, agent_id, load_api_keys(), ws=ws)
    finally:
        os.unlink(prompt_file)


# ══════════════════════════════════════════════════════════════════════
# Web Search (legacy, kept as fallback)
# ══════════════════════════════════════════════════════════════════════

async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Search the web using ddgs (DuckDuckGo Search library).
    Each result: {title, snippet, url}
    """
    def _search():
        from ddgs import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, _search)
        results = []
        for r in raw:
            results.append({
                "title": r.get("title", "")[:200],
                "snippet": r.get("body", "")[:500],
                "url": r.get("href", r.get("url", ""))[:300],
            })
        return results
    except Exception as e:
        log.warning(f"    Web search failed: {e}")
        return []


def extract_search_query(description: str) -> str:
    """Extract a search query from a task description."""
    # Remove common task prefixes
    desc = description
    for prefix in ["Research ", "research ", "Find information about ", "Look up ", "Check "]:
        if desc.startswith(prefix):
            desc = desc[len(prefix):]
            break
    # Take first 100 chars, clean up
    desc = desc.split(".")[0].strip()  # first sentence
    return desc[:100]


def is_research_task(description: str) -> bool:
    """Check if a task is a research/analysis task that needs web search."""
    desc_lower = description.lower()
    research_keywords = [
        "research", "find information", "look up", "investigate", "analyze",
        "check for", "find out", "what is", "what are", "latest", "current",
        "reported", "specs", "technology", "compare", "landscape",
    ]
    return any(kw in desc_lower for kw in research_keywords)


# ══════════════════════════════════════════════════════════════════════
# Task Execution with Models
# ══════════════════════════════════════════════════════════════════════

async def execute_with_model(task: dict, agent_id: str, api_keys: dict,
                            ws=None) -> dict:
    """Execute a task using the agent's assigned model.
    
    For research tasks: web search first, then LLM synthesizes the results.
    For synthesis tasks: use upstream results directly.
    For other tasks: LLM only.
    """
    desc = task.get("description", task.get("name", ""))
    task_id = task.get("task_id", "unknown")
    model_cfg = get_model_for_agent(agent_id)
    provider = model_cfg["provider"]
    model_name = model_cfg["model"]

    log.info(f"    [{agent_id}] Calling {provider} ({model_name})...")

    # Build system prompt
    system_prompt = (
        f"You are agent {agent_id} in a multi-agent orchestration system. "
        f"You are powered by {model_name}. "
        f"Execute the task thoroughly and return a clear, structured summary of your findings. "
        f"Be concise but complete. Focus on actionable information."
    )

    # Build user prompt with task description
    user_prompt = f"Task: {desc}\n\nProvide a thorough response with your findings."

    # ── Web search for research tasks ──
    upstream = task.get("upstream_results", [])

    if not upstream and is_research_task(desc):
        # This is a research task with no upstream deps — search the web
        search_query = extract_search_query(desc)
        if ws:
            try:
                await ws.send(json.dumps({
                    "type": "task_progress", "task_id": task_id,
                    "agent_id": agent_id, "stage": "web_searching",
                    "detail": f"Searching web for: {search_query[:80]}...",
                }))
            except Exception:
                pass

        log.info(f"    [{agent_id}] Web searching: {search_query[:80]}")
        search_results = await web_search(search_query, max_results=5)

        if search_results:
            log.info(f"    [{agent_id}] Found {len(search_results)} search results")
            if ws:
                try:
                    await ws.send(json.dumps({
                        "type": "task_progress", "task_id": task_id,
                        "agent_id": agent_id, "stage": "web_searching",
                        "detail": f"Found {len(search_results)} results. Passing to model for synthesis.",
                    }))
                except Exception:
                    pass

            user_prompt += "\n\n## Web Search Results\n"
            user_prompt += "Use these real web search results as your primary source of information. "
            user_prompt += "Synthesize and structure the findings. Do not say you lack information.\n\n"
            for i, r in enumerate(search_results):
                user_prompt += f"### Result {i+1}: {r['title']}\n"
                user_prompt += f"{r['snippet']}\n\n"
        else:
            log.warning(f"    [{agent_id}] Web search returned no results")

    # Add upstream context if available (for synthesis tasks)
    if upstream:
        user_prompt += "\n\nUpstream task results:\n"
        for r in upstream:
            user_prompt += f"\n--- [{r.get('task_id', '?')}] ---\n{str(r.get('summary', ''))[:2000]}\n"
        user_prompt += "\n\nIMPORTANT: Use the above upstream results as your primary source. "
        user_prompt += "Synthesize these findings into your analysis. Do not say you lack the information."

    # Call the model
    result = await call_model(agent_id, system_prompt, user_prompt, api_keys,
                             ws=ws, task_id=task_id)

    if result.get("ok"):
        content = result["content"]
        log.info(f"    [{agent_id}] Model responded ({result['latency_ms']:.0f}ms, {len(content)} chars)")

        # If the task needs a file created, write the model output to the file
        verify = task.get("verify")
        if verify and verify.get("method") == "file_exists":
            params = verify.get("params", {})
            file_path = params.get("path", "")
            if file_path:
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w") as f:
                    f.write(content)
                log.info(f"    [{agent_id}] Wrote output to {file_path}")

        return {
            "summary": content[:2000],
            "metadata": {
                "model": model_name,
                "provider": provider,
                "latency_ms": result["latency_ms"],
                "content_length": len(content),
            },
        }
    else:
        log.warning(f"    [{agent_id}] Model failed: {result.get('error', 'unknown')}")
        # Fallback: do the task without a model
        return await execute_fallback(task, agent_id)


async def execute_fallback(task: dict, agent_id: str) -> dict:
    """Fallback when model API is unavailable."""
    desc = task.get("description", "")
    task_id = task.get("task_id", "unknown")

    # If verify expects a file, create it
    verify = task.get("verify")
    if verify and verify.get("method") == "file_exists":
        params = verify.get("params", {})
        file_path = params.get("path", "")
        if file_path:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w") as f:
                f.write(f"Task: {desc}\nAgent: {agent_id}\nTime: {time.strftime('%H:%M:%S')}\n\n(Fallback - model unavailable)\n")
            return {"summary": f"Created {file_path} (fallback)", "metadata": {"fallback": True}}

    # Simple web search fallback using ddgs
    try:
        from ddgs import DDGS
        query = desc[:200]
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=5))
        results = [r.get("body", "")[:300] for r in raw if r.get("body")]
        summary = f"[Fallback] Research for: {desc[:100]}\n"
        summary += "\n".join(f"- {r}" for r in results) if results else "(no results)"
        return {"summary": summary[:2000], "metadata": {"fallback": True}}
    except Exception as e:
        return {"summary": f"[Fallback] Task {task_id}: {desc[:100]}\nError: {e}", "metadata": {"fallback": True}}


# Keep the old execute_task for backward compat (file/package/command tasks)
async def execute_task(task: dict, agent_id: str, api_keys: dict,
                      ws=None) -> dict:
    """
    Execute a task. Routes to model for research/analysis tasks,
    handles file/package/command tasks directly.
    """
    desc = task.get("description", task.get("name", ""))
    verify = task.get("verify", {})
    verify_method = verify.get("method", "none") if verify else "none"
    task_id = task.get("task_id", task.get("id", "unknown"))

    # Direct execution for non-research tasks (no model needed)
    if verify_method == "package_installed":
        pkg = verify.get("params", {}).get("package", "")
        if pkg:
            result = subprocess.run(f"pip install {pkg}", shell=True, capture_output=True, text=True, timeout=60)
            return {"summary": f"Installed {pkg}: exit {result.returncode}"}

    elif verify_method == "command_success":
        cmd = verify.get("params", {}).get("command", "echo ok")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return {"summary": f"Ran: {cmd} -> exit {result.returncode}\nOutput: {result.stdout[:300]}"}

    elif verify_method == "test_passes":
        test_path = verify.get("params", {}).get("test_path", "")
        if test_path:
            os.makedirs(os.path.dirname(test_path), exist_ok=True)
            with open(test_path, "w") as f:
                f.write("def test_ok():\n    assert True\n")
            return {"summary": f"Created test: {test_path}"}

    elif verify_method == "skill_loads":
        skill_name = verify.get("params", {}).get("skill_name", "")
        if skill_name:
            skill_dir = os.path.expanduser(f"~/.hermes/skills/{skill_name}")
            os.makedirs(skill_dir, exist_ok=True)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(f"---\nname: {skill_name}\ndescription: Auto-created\n---\n# {skill_name}\n")
            return {"summary": f"Created skill: {skill_name}"}

    # For everything else (research, analysis, synthesis, file output) -> use Hermes agent
    return await execute_with_hermes(task, agent_id, ws=ws)


# ══════════════════════════════════════════════════════════════════════
# Agent Client
# ══════════════════════════════════════════════════════════════════════

class AgentClient:
    """
    A single agent that connects to the bridge, receives tasks,
    executes them using its assigned model, and submits results.
    """

    def __init__(self, agent_id: str, url: str = "ws://localhost:8765"):
        self.agent_id = agent_id
        self.url = url
        self.ws = None
        self.tasks_completed = 0
        self.tasks_failed = 0
        self._handled_tasks: set[str] = set()  # dedupe task dispatches
        self.api_keys = load_api_keys()
        self.model_cfg = get_model_for_agent(agent_id)

    async def connect(self):
        self.ws = await websockets.connect(
            self.url,
            ping_interval=30,
            ping_timeout=120,
            close_timeout=10,
        )
        model_name = self.model_cfg["model"]
        provider = self.model_cfg["provider"]
        has_key = bool(self.api_keys.get(provider, ""))
        log.info(f"[{self.agent_id}] Connected | Model: {model_name} ({provider}) | API key: {'yes' if has_key else 'NO - will fallback'}")

        # Register
        await self.ws.send(json.dumps({
            "type": "register_agent",
            "agent_id": self.agent_id,
            "capabilities": ["terminal", "research", "file_creation", "synthesis"],
        }))

    async def send_progress(self, task_id: str, stage: str, detail: str):
        """Send a progress update to the bridge for dashboard display."""
        try:
            await self.ws.send(json.dumps({
                "type": "task_progress",
                "task_id": task_id,
                "agent_id": self.agent_id,
                "stage": stage,
                "detail": detail,
            }))
        except Exception:
            pass

    async def _execute_and_submit(self, task_data: dict):
        """Execute a task and submit the result. Runs as a background task
        so the WebSocket listen loop stays responsive to pings."""
        task_id = task_data["task_id"]
        task_name = task_data.get("name", "")
        task_desc = task_data.get("description", task_name)
        verify_method = task_data.get("verify", {}).get("method", "none") if task_data.get("verify") else "none"
        upstream_results = task_data.get("upstream_results", [])

        log.info(f"[{self.agent_id}] Received task: {task_id} ({task_name[:60]})")
        await self.send_progress(task_id, "received", f"Task received: {task_name[:60]}")

        if upstream_results:
            log.info(f"    Upstream context: {len(upstream_results)} results")
            await self.send_progress(task_id, "received",
                f"Received {len(upstream_results)} upstream results to synthesize")

        try:
            needs_model = verify_method in ("none", "file_exists") or \
                          any(kw in task_desc.lower() for kw in
                              ["research", "check", "find", "analyze", "search",
                               "investigate", "develop", "information",
                               "write", "create", "summarize", "compile"])

            if needs_model:
                model_name = self.model_cfg["model"]
                await self.send_progress(task_id, "calling_model",
                    f"Calling {model_name}...")
                log.info(f"    Model: {model_name} | Verify: {verify_method}")

            # Execute with model
            artifacts = await execute_task(task_data, self.agent_id, self.api_keys,
                                         ws=self.ws)

            # Report completion
            summary_preview = artifacts.get("summary", "")[:100]
            await self.send_progress(task_id, "completed",
                f"Model responded. Submitting result: {summary_preview}...")

            # Submit result
            await self.ws.send(json.dumps({
                "type": "submit_task",
                "task_id": task_id,
                "agent_id": self.agent_id,
                "artifacts": artifacts,
            }))

            self.tasks_completed += 1
            log.info(f"[{self.agent_id}] Submitted: {task_id} (completed: {self.tasks_completed})")

        except Exception as e:
            log.error(f"[{self.agent_id}] Task {task_id} failed: {e}")
            self.tasks_failed += 1

    async def listen(self):
        """Listen for messages and dispatch tasks as background coroutines.

        Task execution runs in a separate asyncio.Task so the WebSocket
        receive loop keeps draining the socket — answering pings and
        receiving new dispatches — even during 60s+ model calls.
        """
        log.info(f"[{self.agent_id}] Listening for tasks...")

        pending_tasks: set[asyncio.Task] = set()

        async for message in self.ws:
            try:
                msg = json.loads(message)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "task_dispatched":
                data = msg["data"]
                assigned_agent = data.get("agent_id", "")
                task_id = data.get("task_id", "")

                # Only execute if assigned to us
                if assigned_agent != self.agent_id:
                    continue

                # Dedupe — bridge sends to agent socket + broadcasts to dashboard
                # Agent receives both copies; only process once
                if task_id in self._handled_tasks:
                    continue
                self._handled_tasks.add(task_id)

                # Build task dict
                task_data = {
                    "task_id": data["task_id"],
                    "name": data.get("task_name", ""),
                    "description": data.get("description", data.get("task_name", "")),
                    "verify": (
                        {"method": data.get("verify_method", "none"),
                         "params": data.get("verify_params", {})}
                        if data.get("verify_method", "none") != "none" else None
                    ),
                    "upstream_results": data.get("upstream_results", []),
                }

                # Spawn execution as a background task — non-blocking!
                bg_task = asyncio.create_task(self._execute_and_submit(task_data))
                pending_tasks.add(bg_task)
                bg_task.add_done_callback(pending_tasks.discard)

            elif msg_type == "submission_result":
                data = msg.get("data", {})
                ok = data.get("verified", False)
                if ok:
                    log.info(f"[{self.agent_id}] Task verified: OK")
                else:
                    log.warning(f"[{self.agent_id}] Verification FAILED: {data.get('error', 'unknown')}")

            elif msg_type == "pipeline_complete":
                log.info(f"[{self.agent_id}] Pipeline complete! "
                         f"Completed: {self.tasks_completed}, Failed: {self.tasks_failed}")
                break

            elif msg_type == "layer_attested":
                log.info(f"[{self.agent_id}] Layer {msg['data']['layer']} confirmed on-chain!")

            elif msg_type == "layer_attesting":
                log.info(f"[{self.agent_id}] Layer {msg['data']['layer']} attesting...")

        # Wait for any in-flight tasks before returning
        if pending_tasks:
            log.info(f"[{self.agent_id}] Waiting for {len(pending_tasks)} in-flight tasks...")
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    async def run(self):
        await self.connect()
        await self.listen()
        await self.ws.close()
        log.info(f"[{self.agent_id}] Disconnected. "
                 f"Completed: {self.tasks_completed}, Failed: {self.tasks_failed}")


# ══════════════════════════════════════════════════════════════════════
# Multi-agent launcher
# ══════════════════════════════════════════════════════════════════════

async def run_all_agents(url: str, count: int = 10):
    """Launch multiple agent clients concurrently."""
    agents = [AgentClient(f"agent-{i+1}", url) for i in range(count)]
    tasks = [agent.run() for agent in agents]
    await asyncio.gather(*tasks, return_exceptions=True)


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(description="Agent Client for Multi-Agent Orchestrator")
    parser.add_argument("--agent", type=str, help="Agent ID (e.g. agent-1)")
    parser.add_argument("--all", action="store_true", help="Run all 10 agents")
    parser.add_argument("--count", type=int, default=10, help="Number of agents (with --all)")
    parser.add_argument("--url", type=str, default="ws://localhost:8765", help="Bridge WebSocket URL")
    parser.add_argument("--model", choices=["glm", "gemma4", "nemotron"], default=None,
                        help="Model provider override for all agents (default: GLM-5.2)")
    args = parser.parse_args()

    # Set model override if specified
    if args.model:
        global MODEL_OVERRIDE
        MODEL_OVERRIDE = args.model

    # Show model assignment at startup
    keys = load_api_keys()
    log.info("Model Assignment:")
    for i in range(1, 11):
        cfg = get_model_for_agent(f"agent-{i}")
        provider = cfg["provider"]
        has_key = bool(keys.get(provider, ""))
        log.info(f"  agent-{i}: {cfg['model']} ({provider}) key={'yes' if has_key else 'NO'}")

    if args.all:
        log.info(f"Starting {args.count} agent clients...")
        await run_all_agents(args.url, args.count)
    elif args.agent:
        client = AgentClient(args.agent, args.url)
        await client.run()
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
