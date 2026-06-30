#!/usr/bin/env python3
"""
Planner: Decomposes a single user prompt into subtasks with dependencies.
Uses Nemotron 3 Ultra to reason about the prompt and identify:
  - What distinct tasks need to be done
  - Which tasks depend on others
  - Which agent should handle each task

Supports two input modes:
  1. Text prompt: "Research IBM sub-nm chip and Huawei LogicFolding..."
  2. Image input: photo uploaded, analyzed by a vision model, then decomposed

Example:
  User prompt: "Research IBM sub-nm chip and Huawei LogicFolding, then
  create a report merging both."

  Nemotron output:
    [
      {"id": "task_00", "name": "IBM sub-nm chip research", "agent_id": "agent-1",
       "description": "Research IBM sub-nm chip technology...", "dependencies": []},
      {"id": "task_01", "name": "Huawei LogicFolding research", "agent_id": "agent-2",
       "description": "Research Huawei LogicFolding technology...", "dependencies": []},
      {"id": "task_02", "name": "Report synthesis", "agent_id": "agent-3",
       "description": "Merge findings and create a comprehensive report...", "dependencies": ["task_00", "task_01"]},
    ]
"""

import asyncio
import json
import os
import sys
import time
import logging
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("planner")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_client import load_api_keys, MODEL_CONFIG

PLANNER_SYSTEM_PROMPT = """You are a task planner for a multi-agent orchestration system with 10 AI agents.

Your job: take a user's natural language request and decompose it into subtasks that can be executed by separate agents in parallel or in sequence.

## Reasoning Process (do this internally before producing output)

1. Identify the GOAL: What is the user trying to achieve?
2. Identify the INPUTS: What subjects, topics, or data sources are involved?
3. Identify PARALLEL WORK: What can be done independently at the same time?
   - Multiple research subjects -> separate parallel tasks
   - Different data sources -> separate tasks
   - Independent analysis angles -> separate tasks
4. Identify DEPENDENCIES: What task needs results from another task?
   - A synthesis/merge task depends on ALL tasks it synthesizes
   - A task that needs information from another task depends on that task
   - A review/validation task depends on the task it reviews
5. Identify the OUTPUT: Is there a final synthesis, report, or decision task?
6. Assign agents: parallel tasks get different agents (agent-1, agent-2, ...). A dependent task can reuse an agent that is now free.

## Key Principles

- MAXIMIZE PARALLELISM: If two tasks don't depend on each other, they should be separate tasks with no dependencies. Do NOT serialize work that can be done in parallel.
- GRANULARITY: Each task should be a single coherent unit of work that one agent can complete in one LLM call. "Research X and Y and Z" = 3 tasks, not 1.
- REAL DESCRIPTIONS: The description field should be detailed enough that an agent can execute it without seeing the original prompt. Include the specific topic, what to look for, and what format to return.
- DEPENDENCIES ARE EXPLICIT: A task that needs results from task_00 and task_01 must list both in dependencies. A task with no dependencies can start immediately.
- ALWAYS HAVE A SYNTHESIS TASK if the user's request involves combining, merging, or creating something from multiple sources.

## Examples

User: "Research NVIDIA Blackwell and AMD MI400, then compare their specs"
Output:
[
  {"id": "task_00", "name": "Research NVIDIA Blackwell", "agent_id": "agent-1", "description": "Research NVIDIA's Blackwell GPU architecture. Find specs, performance benchmarks, release timeline, and key technical innovations. Return a structured summary.", "dependencies": []},
  {"id": "task_01", "name": "Research AMD MI400", "agent_id": "agent-2", "description": "Research AMD's MI400 GPU. Find specs, performance benchmarks, release timeline, and key technical innovations. Return a structured summary.", "dependencies": []},
  {"id": "task_02", "name": "Compare specs", "agent_id": "agent-3", "description": "Using the research on NVIDIA Blackwell and AMD MI400, create a detailed comparison table covering: compute performance, memory bandwidth, power consumption, price, and availability. Highlight key differences and recommend which is better for different use cases.", "dependencies": ["task_00", "task_01"]}
]

User: "Build a Python web scraper, install dependencies, write tests, then deploy"
Output:
[
  {"id": "task_00", "name": "Install dependencies", "agent_id": "agent-1", "description": "Install required Python packages: requests, beautifulsoup4, pytest. Verify they are installed with pip show.", "dependencies": []},
  {"id": "task_01", "name": "Write web scraper", "agent_id": "agent-2", "description": "Write a Python web scraper using requests and beautifulsoup4. The scraper should fetch a URL, parse the HTML, and extract structured data. Save as scraper.py.", "dependencies": ["task_00"]},
  {"id": "task_02", "name": "Write tests", "agent_id": "agent-3", "description": "Write pytest tests for the web scraper. Test: URL fetching, HTML parsing, data extraction. Save as test_scraper.py.", "dependencies": ["task_01"]},
  {"id": "task_03", "name": "Deploy scraper", "agent_id": "agent-4", "description": "Package the scraper for deployment. Create a Dockerfile and deployment script. Verify the deployment works.", "dependencies": ["task_01", "task_02"]}
]

User: "Analyze the impact of the new SEC climate disclosure rule on tech companies"
Output:
[
  {"id": "task_00", "name": "Research SEC climate rule", "agent_id": "agent-1", "description": "Research the SEC climate disclosure rule. Find: what it requires, when it takes effect, what companies must report, and penalties for non-compliance.", "dependencies": []},
  {"id": "task_01", "name": "Analyze tech company exposure", "agent_id": "agent-2", "description": "Identify which tech companies are most affected by climate disclosure requirements. Focus on: data center energy usage, supply chain emissions, and companies with limited current disclosures.", "dependencies": []},
  {"id": "task_02", "name": "Assess compliance impact", "agent_id": "agent-3", "description": "Estimate the impact of compliance costs on affected companies. Consider: reporting infrastructure costs, supply chain changes, and operational disruptions.", "dependencies": ["task_00", "task_01"]},
  {"id": "task_03", "name": "Create action plan", "agent_id": "agent-4", "description": "Using the rule analysis, company exposure, and impact assessment, create an action plan with recommendations for: monitoring, engagement, and compliance tracking.", "dependencies": ["task_00", "task_01", "task_02"]}
]

## Output Format

You MUST respond with ONLY a JSON array. No markdown, no explanation, no code fences.

[
  {
    "id": "task_00",
    "name": "Short task name",
    "agent_id": "agent-1",
    "description": "Detailed description of what this agent should do, including the specific topic, what to look for, and what to return.",
    "dependencies": []
  }
]

CRITICAL: Return ONLY the JSON array."""


async def plan_from_image(image_b64: str, user_prompt: str = "", use_nemotron: bool = True) -> list[dict]:
    """
    Analyze an uploaded image with a vision model, then decompose into subtasks.
    Step 1: Vision model describes what's in the image (with user's prompt as context).
    Step 2: The image description + user's prompt are passed to the planner together.
    """
    api_keys = load_api_keys()

    # Step 1: Describe the image using Cerebras gemma-4-31b (vision-capable)
    log.info(f"Planning from image (prompt: {len(user_prompt)} chars): calling Cerebras gemma-4-31b vision model...")
    description = await _describe_image(image_b64, api_keys.get("gemma4", ""), user_prompt)
    if not description:
        log.warning("Vision model failed, cannot plan from image")
        return []

    log.info(f"Vision model described image ({len(description)} chars), decomposing...")

    # Step 2: Combine image description with user's prompt for the planner
    if user_prompt.strip():
        combined = f"User request: {user_prompt.strip()}\n\nImage analysis:\n{description}"
    else:
        combined = description

    return await plan_tasks(combined, use_nemotron=use_nemotron)


async def _describe_image(image_b64: str, api_key: str, user_prompt: str = "") -> str:
    """
    Call a vision-capable model to describe an image.
    Uses Cerebras gemma-4-31b (supports image_url in messages).
    If user_prompt is provided, the vision model uses it as context for what to focus on.
    Returns a text description of the image.
    """
    if not api_key:
        log.error("No Cerebras API key for vision model")
        return ""

    url = "https://api.cerebras.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if user_prompt.strip():
        vision_text = (
            "You analyze images and describe them in detail for a multi-agent research system. "
            "Describe: what is shown, any text visible, technical details, data presented, and key topics that could be researched. "
            "Be thorough and specific. This description will be used to decompose into research tasks.\n\n"
            f"The user has provided this request alongside the image: \"{user_prompt.strip()}\"\n"
            "Analyze the image with this request in mind. Focus on aspects of the image relevant to what the user wants. "
            "Also describe anything else important in the image.\n\n"
            "Describe everything relevant for research task decomposition. What is it? What topics does it cover? What could agents research about it?"
        )
    else:
        vision_text = (
            "You analyze images and describe them in detail for a multi-agent research system. "
            "Describe: what is shown, any text visible, technical details, data presented, and key topics that could be researched. "
            "Be thorough and specific. This description will be used to decompose into research tasks.\n\n"
            "Analyze this image and describe everything relevant for research task decomposition. "
            "What is it? What topics does it cover? What could agents research about it?"
        )

    payload = {
        "model": "gemma-4-31b",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": vision_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                ],
            },
        ],
        "max_tokens": 1500,
        "temperature": 0.3,
    }

    start = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                body = await resp.json()
                elapsed = (time.time() - start) * 1000

        if resp.status != 200:
            err = str(body)[:200] if body else f"HTTP {resp.status}"
            log.error(f"Vision model error: {resp.status}: {err}")
            return ""

        content = body["choices"][0]["message"]["content"]
        log.info(f"Vision model responded in {elapsed:.0f}ms ({len(content)} chars)")
        return content

    except Exception as e:
        log.error(f"Vision model failed: {type(e).__name__}: {e}")
        return ""


async def plan_tasks(user_prompt: str, use_nemotron: bool = True) -> list[dict]:
    """
    Call Nemotron 3 Ultra (NVIDIA NIM) to decompose the user prompt into subtasks.
    Falls back to GLM-5.2 if Nemotron is unavailable or times out (30s).
    """
    api_keys = load_api_keys()

    if use_nemotron and api_keys.get("nemotron"):
        log.info("Planning with Nemotron 3 Ultra...")
        result = await _call_nemotron_planner(user_prompt, api_keys["nemotron"])
        if result.get("ok"):
            tasks = _parse_tasks(result["content"])
            if tasks:
                log.info(f"Nemotron planned {len(tasks)} tasks")
                return tasks
            log.warning("Nemotron response didn't parse, trying GLM...")
        else:
            log.warning(f"Nemotron planning failed: {result.get('error')}, trying GLM...")

    # Fallback to GLM
    if api_keys.get("glm"):
        log.info("Planning with GLM-5.2...")
        result = await _call_glm_planner(user_prompt, api_keys["glm"])
        if result.get("ok"):
            tasks = _parse_tasks(result["content"])
            if tasks:
                log.info(f"GLM planned {len(tasks)} tasks")
                return tasks

    # Last resort: heuristic parsing
    log.warning("LLM planning failed, using heuristic decomposition...")
    return _heuristic_decompose(user_prompt)


async def _call_nemotron_planner(user_prompt: str, api_key: str) -> dict:
    """
    Call Nemotron 3 Ultra to decompose the prompt.
    
    Routes through NemoClaw sandbox (inference.local) which provides
    better inference routing than direct NVIDIA NIM API calls.
    The sandbox's inference.local proxy avoids the timeout issues
    seen on direct API calls to integrate.api.nvidia.com.
    """
    # Build a single-line prompt (nemohermes exec doesn't support newlines)
    # Use condensed system prompt for NemoClaw (no newlines allowed)
    condensed_prompt = (
        "You are a task planner for a multi-agent system with 10 AI agents. "
        "Decompose the user's request into subtasks. "
        "Rules: maximize parallelism (independent tasks get no dependencies), "
        "each task is one coherent unit for one agent, "
        "always create a synthesis task if the request involves combining results, "
        "dependencies must be explicit (list task IDs). "
        "Return ONLY a JSON array: "
        '[{"id":"task_00","name":"Short name","agent_id":"agent-1","description":"Detailed description","dependencies":[]}]'
    )
    plan_prompt = f"{condensed_prompt} Decompose this request: {user_prompt}"
    # Escape single quotes for shell
    plan_prompt_escaped = plan_prompt.replace("'", "'\\''")
    # Remove any newlines (nemohermes exec rejects them)
    plan_prompt_escaped = plan_prompt_escaped.replace("\n", " ").replace("\r", "")
    
    cmd = (
        f"nemohermes mao-hermes exec -- "
        f"hermes chat -q '{plan_prompt_escaped}'"
    )
    
    start = time.time()
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
        elapsed = (time.time() - start) * 1000
        
        if proc.returncode != 0:
            err = stderr.decode()[:200] if stderr else f"exit code {proc.returncode}"
            return {"ok": False, "error": f"NemoClaw exec failed: {err}"}
        
        output = stdout.decode()
        
        # Extract the response from Hermes output
        # Hermes wraps output in ╭─ ⚕ Hermes ── ... ─╮ blocks
        content = _extract_hermes_output(output)
        if not content:
            # Fallback: try to find JSON array in raw output
            content = output
        
        log.info(f"Nemotron (via NemoClaw) responded in {elapsed:.0f}ms ({len(content)} chars)")
        return {"ok": True, "content": content, "latency_ms": elapsed}
        
    except asyncio.TimeoutError:
        return {"ok": False, "error": "NemoClaw exec timed out (90s)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _extract_hermes_output(raw: str) -> str:
    """Extract the model response from Hermes CLI output."""
    # Hermes wraps output between ╭─ ⚕ Hermes ── and ─╮
    lines = raw.split("\n")
    in_response = False
    response_lines = []
    for line in lines:
        if "⚕ Hermes" in line:
            in_response = True
            continue
        if in_response:
            if "─╯" in line or "──╯" in line:
                break
            # Strip leading whitespace/padding
            response_lines.append(line.strip())
    return "\n".join(response_lines).strip() if response_lines else ""


async def _call_glm_planner(user_prompt: str, api_key: str) -> dict:
    """Call GLM-5.2 to decompose the prompt."""
    config = MODEL_CONFIG["glm"]
    url = config["url"]
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": f"Decompose this request into subtasks:\n\n{user_prompt}"},
        ],
        "max_tokens": 2000,
        "temperature": 0.2,
    }

    start = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                body = await resp.json()
                elapsed = (time.time() - start) * 1000

        if resp.status != 200:
            err = str(body)[:200] if body else f"HTTP {resp.status}"
            return {"ok": False, "error": f"GLM {resp.status}: {err}"}

        content = body["choices"][0]["message"]["content"]
        log.info(f"GLM responded in {elapsed:.0f}ms ({len(content)} chars)")
        return {"ok": True, "content": content, "latency_ms": elapsed}

    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _parse_tasks(content: str) -> list[dict]:
    """
    Parse the LLM response into a list of task dicts.
    Handles markdown code fences, extra text, etc.
    """
    # Strip markdown code fences if present
    content = content.strip()
    if content.startswith("```"):
        # Remove ```json or ``` prefix
        lines = content.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    # Find the JSON array
    start_idx = content.find("[")
    end_idx = content.rfind("]")
    if start_idx == -1 or end_idx == -1:
        log.error(f"Could not find JSON array in response:\n{content[:500]}")
        return []

    json_str = content[start_idx:end_idx + 1]

    try:
        tasks = json.loads(json_str)
    except json.JSONDecodeError as e:
        log.error(f"JSON parse error: {e}")
        log.error(f"Content: {json_str[:500]}")
        return []

    # Validate and normalize tasks
    valid_tasks = []
    for i, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue

        task_id = task.get("id", f"task_{i:02d}")
        # Normalize task ID
        if not task_id.startswith("task_"):
            task_id = f"task_{i:02d}"

        valid_tasks.append({
            "id": task_id,
            "name": task.get("name", f"Task {i}"),
            "agent_id": task.get("agent_id", f"agent-{i+1}"),
            "description": task.get("description", task.get("name", "")),
            "dependencies": task.get("dependencies", []),
            "verify": None,
            "cost_weight": 2.0 if task.get("dependencies") else 1.0,
            "sla_ms": 30000,
        })

    return valid_tasks


def _heuristic_decompose(user_prompt: str) -> list[dict]:
    """
    Fallback: simple heuristic decomposition based on keywords.
    Handles "research X and Y" by splitting on "and" within the clause.
    """
    prompt_lower = user_prompt.lower()
    import re

    tasks = []
    task_idx = 0
    research_tasks = []

    # Match "research X and Y" or "get information on X and Y"
    # Capture only up to the first sentence end (. ! ?) or merge/then keyword
    clause_pattern = r"(?:research|get information on|find information about|look up|check for information about)\s+(.+?)(?:\s+(?:and then|then|\. merge|, merge|and merge|and create|and develop),?|[.!?]|\.$|$)"

    clause_match = re.search(clause_pattern, user_prompt, re.IGNORECASE | re.DOTALL)
    if clause_match:
        clause = clause_match.group(1).strip()
        # Split on " and " — keep a split only if BOTH sides start with a capitalized word
        # (proper noun / brand name like "Huawei" or "IBM")
        # If a side starts with a lowercase verb ("list", "report"), merge it back
        raw_subjects = re.split(r'\s+and\s+', clause)
        subjects = []
        for s in raw_subjects:
            s = s.strip().rstrip('.,;!?')
            if len(s) < 5:
                continue
            words = s.split()
            first_word = words[0] if words else ''
            # Check if first word is capitalized (proper noun) or a tech keyword
            is_proper = first_word[0].isupper() if first_word else False
            # Common verb starts that are NOT separate research subjects
            verb_starts = ('list', 'provide', 'report', 'include', 'describe', 'explain',
                          'analyze', 'identify', 'determine', 'create', 'merge', 'develop')
            is_verb = first_word.lower() in verb_starts

            if is_verb or (not is_proper and subjects):
                # This fragment is a continuation, not a new subject — merge back
                if subjects:
                    subjects[-1] += ' and ' + s
                else:
                    subjects.append(s)
            else:
                subjects.append(s)

        for subject in subjects:
            tid = f"task_{task_idx:02d}"
            research_tasks.append(tid)
            tasks.append({
                "id": tid,
                "name": f"Research: {subject[:50]}",
                "agent_id": f"agent-{task_idx + 1}",
                "description": f"Research {subject}. Provide detailed findings on fabrication specs, requirements, and technology details.",
                "dependencies": [],
                "verify": None,
                "cost_weight": 1.0,
                "sla_ms": 30000,
            })
            task_idx += 1

    # If no research tasks found, create a single task
    if not tasks:
        tasks.append({
            "id": "task_00",
            "name": "Execute request",
            "agent_id": "agent-1",
            "description": user_prompt,
            "dependencies": [],
            "verify": None,
            "cost_weight": 1.0,
            "sla_ms": 30000,
        })
        return tasks

    # Look for synthesis/merge/create keywords
    if any(kw in prompt_lower for kw in ["merge", "synthes", "create", "develop", "combine", "report"]):
        tid = f"task_{task_idx:02d}"
        # Extract the synthesis instruction
        merge_idx = prompt_lower.find("merge") if "merge" in prompt_lower else prompt_lower.find("create")
        synthesis_desc = user_prompt[merge_idx:] if merge_idx >= 0 else "Merge and synthesize the research findings."
        tasks.append({
            "id": tid,
            "name": "Synthesis & Report Generation",
            "agent_id": f"agent-{task_idx + 1}",
            "description": f"Based on the research results, {synthesis_desc}",
            "dependencies": research_tasks,
            "verify": None,
            "cost_weight": 2.0,
            "sla_ms": 30000,
        })

    return tasks


# ── Self-test ────────────────────────────────────────────────────────

async def test():
    prompt = (
        "get information on Huawei's LogicFolding technology and IBM sub nm technology "
        "reported today and list all the fabrication specs and requirements it has up to "
        "now reported for 3d stack or bonding. Merge the two information about LogicFolding "
        "by Huawei and IBM sub nm chip and create a synthesis report covering "
        "technical specs, fabrication requirements, and comparison of both approaches"
    )

    print(f"=== Planner Test ===")
    print(f"User prompt: {prompt[:100]}...\n")

    tasks = await plan_tasks(prompt)

    print(f"\nDecomposed into {len(tasks)} tasks:\n")
    for t in tasks:
        deps = f" deps={t['dependencies']}" if t['dependencies'] else ""
        print(f"  {t['id']}: {t['name']} [{t['agent_id']}]{deps}")
        print(f"    {t['description'][:100]}...")
        print()

    # Show layers
    from task_dag import build_dag, get_layer_summary
    task_dict, layers = build_dag(tasks)
    summaries = get_layer_summary(task_dict, layers)
    print(f"DAG: {len(tasks)} tasks, {len(layers)} layers")
    for s in summaries:
        print(f"  Layer {s['layer']}: {s['count']} tasks | agents={s['agents']}")


if __name__ == "__main__":
    asyncio.run(test())
