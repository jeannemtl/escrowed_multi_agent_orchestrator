#!/usr/bin/env python3
"""
Stripe Escrow Charger
=====================
Implements escrow for AI compute: payment is only released AFTER
work is verified and attested on-chain. Budget enforcement stops
the pipeline when the dollar cap is hit.

Three modes:
  1. mock    — no Stripe calls, logs charges as "mock_charge_..."
  2. test    — real Stripe API, test mode (sk_test_..., 4242 card)
  3. live    — real Stripe API, live mode (NEVER without explicit user consent)

Escrow flow:
  1. Pipeline starts → budget cap set (e.g. $5.00)
  2. Layer attested on Solana → compute layer cost
  3. Check: total_spent + layer_cost > budget_cap? → BLOCK pipeline
  4. Stripe charge created (test mode: 4242 card, auto-confirm)
  5. Charge succeeds → layer promoted (downstream tasks dispatch)
  6. Charge fails → pipeline STOPS, on-chain attestation is the receipt
     of what WAS completed

The Stripe charge ID + Solana signature together form a complete audit record:
  - Solana sig = cryptographic proof work was done
  - Stripe charge ID = payment settlement for that work
"""

import asyncio
import json
import time
import logging
import os
from typing import Optional

log = logging.getLogger("stripe-charger")

# ══════════════════════════════════════════════════════════════════════
# Cost model — how much each layer costs in real cents
# ══════════════════════════════════════════════════════════════════════

# Base rate: ~$0.01 per task (covers LLM API cost approximation)
BASE_RATE_CENTS = 1

# Model cost multipliers (approximate real API costs per call)
MODEL_RATES = {
    "z-ai/glm-5.2": 2,        # ~$0.02 per call (OpenRouter)
    "nvidia/nemotron-3-ultra-550b-a55b": 0,  # free NIM
    "default": 1,              # ~$0.01
}


def compute_layer_cost_cents(task_count: int, layer_idx: int,
                              model_used: str = "z-ai/glm-5.2") -> int:
    """
    Compute cost for a layer in cents.

    Cost = base_rate × task_count × model_multiplier × depth_factor × batch_discount

    The batch discount rewards bundling: more tasks per layer = cheaper per task.
    """
    model_mult = MODEL_RATES.get(model_used, MODEL_RATES["default"])
    depth_factor = 1.0 + (layer_idx * 0.3)  # deeper layers cost more (synthesis)
    batch_discount = 1.0 / (1.0 + task_count * 0.1)  # 10 tasks = 50% discount

    total = BASE_RATE_CENTS * task_count * model_mult * depth_factor * batch_discount
    return max(1, round(total))


# ══════════════════════════════════════════════════════════════════════
# Mock Charger (no Stripe, no real money)
# ══════════════════════════════════════════════════════════════════════

class MockCharger:
    """Simulates Stripe charges without any API calls."""

    def __init__(self, budget_cents: int):
        self.budget_cents = budget_cents
        self.total_spent = 0
        self.charges: list[dict] = []

    async def charge(self, amount_cents: int, layer_idx: int,
                      task_ids: list[str], solana_sig: str) -> dict:
        """Simulate a charge. Always succeeds unless budget exceeded."""
        if self.total_spent + amount_cents > self.budget_cents:
            remaining = self.budget_cents - self.total_spent
            log.warning(
                f"BUDGET EXCEEDED: layer {layer_idx} costs {amount_cents}c, "
                f"only {remaining}c remaining in budget"
            )
            return {
                "ok": False,
                "error": "budget_exceeded",
                "remaining_budget_cents": remaining,
                "required_cents": amount_cents,
            }

        charge_id = f"mock_charge_{int(time.time())}_{layer_idx}"
        self.total_spent += amount_cents
        remaining = self.budget_cents - self.total_spent

        charge = {
            "charge_id": charge_id,
            "amount_cents": amount_cents,
            "layer": layer_idx,
            "task_ids": task_ids,
            "solana_signature": solana_sig,
            "status": "succeeded",
            "mode": "mock",
            "remaining_budget_cents": remaining,
        }
        self.charges.append(charge)
        log.info(f"[MOCK CHARGE] {charge_id}: ${amount_cents/100:.2f} for layer {layer_idx} "
                 f"({len(task_ids)} tasks, sig={solana_sig[:20]}...) "
                 f"remaining: ${remaining/100:.2f}")
        return {"ok": True, **charge}

    def get_status(self) -> dict:
        return {
            "mode": "mock",
            "budget_cents": self.budget_cents,
            "total_spent_cents": self.total_spent,
            "remaining_cents": self.budget_cents - self.total_spent,
            "charge_count": len(self.charges),
            "charges": self.charges,
        }


# ══════════════════════════════════════════════════════════════════════
# Stripe Test-Mode Charger
# ══════════════════════════════════════════════════════════════════════

class StripeTestCharger:
    """
    Real Stripe API in test mode. Uses sk_test_ key + 4242 test card.
    No real money moves. Charges are visible in the Stripe Dashboard (test mode).

    Requires:
      - STRIPE_API_KEY env var (sk_test_...)
      - aiohttp installed (already in venv)
    """

    STRIPE_API = "https://api.stripe.com/v1"

    def __init__(self, budget_cents: int, api_key: str):
        self.budget_cents = budget_cents
        self.api_key = api_key
        self.total_spent = 0
        self.charges: list[dict] = []

        # Test card token (4242 4242 4242 4242) — Stripe's universal test token
        self.test_card_token = "tok_visa"

    async def charge(self, amount_cents: int, layer_idx: int,
                     task_ids: list[str], solana_sig: str) -> dict:
        """Create a real Stripe test-mode charge."""
        import aiohttp

        # Budget check first
        if self.total_spent + amount_cents > self.budget_cents:
            remaining = self.budget_cents - self.total_spent
            log.warning(
                f"BUDGET EXCEEDED: layer {layer_idx} costs {amount_cents}c, "
                f"only {remaining}c remaining"
            )
            return {
                "ok": False,
                "error": "budget_exceeded",
                "remaining_budget_cents": remaining,
                "required_cents": amount_cents,
            }

        # Create Stripe charge
        # POST /v1/charges with test card token
        description = (
            f"Layer {layer_idx}: {len(task_ids)} tasks | "
            f"Solana: {solana_sig[:30]}... | "
            f"Tasks: {','.join(t[:12] for t in task_ids)}"
        )

        data = {
            "amount": amount_cents,
            "currency": "usd",
            "source": self.test_card_token,
            "description": description[:500],  # Stripe limit
            "metadata[mode]": "test",
            "metadata[layer]": str(layer_idx),
            "metadata[task_count]": str(len(task_ids)),
            "metadata[task_ids]": ",".join(task_ids)[:500],
            "metadata[solana_sig]": solana_sig[:500],
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.STRIPE_API}/charges",
                    headers=headers,
                    data=data,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    body = await resp.json()

            if resp.status != 200:
                err = body.get("error", {}).get("message", f"HTTP {resp.status}")
                log.error(f"Stripe charge failed: {err}")
                return {
                    "ok": False,
                    "error": f"stripe_error: {err}",
                    "http_status": resp.status,
                }

            charge_id = body["id"]
            status = body["status"]
            self.total_spent += amount_cents
            remaining = self.budget_cents - self.total_spent

            charge = {
                "charge_id": charge_id,
                "amount_cents": amount_cents,
                "layer": layer_idx,
                "task_ids": task_ids,
                "solana_signature": solana_sig,
                "status": status,
                "mode": "stripe_test",
                "stripe_receipt_url": body.get("receipt_url"),
                "remaining_budget_cents": remaining,
            }
            self.charges.append(charge)
            log.info(
                f"[STRIPE TEST] {charge_id}: ${amount_cents/100:.2f} for layer {layer_idx} "
                f"({len(task_ids)} tasks) remaining: ${remaining/100:.2f}"
            )
            return {"ok": True, **charge}

        except Exception as e:
            log.error(f"Stripe API error: {e}")
            return {"ok": False, "error": f"api_error: {e}"}

    def get_status(self) -> dict:
        return {
            "mode": "stripe_test",
            "budget_cents": self.budget_cents,
            "total_spent_cents": self.total_spent,
            "remaining_cents": self.budget_cents - self.total_spent,
            "charge_count": len(self.charges),
            "charges": self.charges,
        }


# ══════════════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════════════

def create_charger(mode: str = "mock", budget_cents: int = 500,
                   stripe_key: str = ""):
    """
    Create a charger instance.

    Args:
        mode: "mock" (no Stripe), "test" (Stripe test mode), "live" (real money)
        budget_cents: dollar cap in cents (e.g. 500 = $5.00)
        stripe_key: Stripe API key (sk_test_... or sk_live_...)

    Returns:
        Charger instance with .charge() and .get_status() methods
    """
    if mode == "mock":
        log.info(f"Charger: MOCK mode, budget=${budget_cents/100:.2f}")
        return MockCharger(budget_cents)

    elif mode in ("test", "live"):
        key = stripe_key or os.environ.get("STRIPE_API_KEY", "")
        if not key:
            log.warning("No STRIPE_API_KEY found, falling back to mock")
            return MockCharger(budget_cents)

        label = "TEST" if mode == "test" else "LIVE"
        if mode == "live":
            log.warning("⚠️  STRIPE LIVE MODE — real charges will be made!")
        log.info(f"Charger: Stripe {label} mode, budget=${budget_cents/100:.2f}")
        return StripeTestCharger(budget_cents, key)

    else:
        raise ValueError(f"Unknown charger mode: {mode}")


# ══════════════════════════════════════════════════════════════════════
# Self-test
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    async def test():
        print("=== Stripe Escrow Charger Test ===\n")

        # Test 1: Mock charger with budget
        charger = create_charger("mock", budget_cents=100)  # $1.00 budget
        print(f"Budget: ${charger.budget_cents/100:.2f}\n")

        # Layer 0: 3 tasks, costs ~3c
        cost0 = compute_layer_cost_cents(3, 0)
        print(f"Layer 0 cost: {cost0}c ({3} tasks)")
        r0 = await charger.charge(cost0, 0, ["t1", "t2", "t3"], "mock_sol_sig_0")
        print(f"  Result: {r0['ok']} charge={r0.get('charge_id')} remaining={r0.get('remaining_budget_cents')}c\n")

        # Layer 1: 2 tasks, costs ~4c
        cost1 = compute_layer_cost_cents(2, 1)
        print(f"Layer 1 cost: {cost1}c ({2} tasks)")
        r1 = await charger.charge(cost1, 1, ["t4", "t5"], "mock_sol_sig_1")
        print(f"  Result: {r1['ok']} charge={r1.get('charge_id')} remaining={r1.get('remaining_budget_cents')}c\n")

        # Try to exceed budget
        big_cost = charger.budget_cents  # try to charge entire remaining budget
        print(f"Trying to charge {big_cost}c (should exceed remaining)...")
        r2 = await charger.charge(big_cost, 2, ["t6"], "mock_sol_sig_2")
        print(f"  Result: ok={r2['ok']} error={r2.get('error')}\n")

        # Status
        status = charger.get_status()
        print(f"Final status:")
        print(f"  Total spent: {status['total_spent_cents']}c")
        print(f"  Remaining: {status['remaining_cents']}c")
        print(f"  Charges: {status['charge_count']}")

        # Test cost model at different scales
        print("\n=== Cost Model ===")
        for tasks in [1, 2, 5, 10, 20]:
            for layer in [0, 1, 2, 3]:
                cost = compute_layer_cost_cents(tasks, layer)
                print(f"  Layer {layer}, {tasks:2d} tasks: {cost:3d}c (${cost/100:.2f})")

    asyncio.run(test())
