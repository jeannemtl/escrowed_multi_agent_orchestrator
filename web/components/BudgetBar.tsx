'use client';

import type { BudgetData } from '@/types/messages';

interface BudgetBarProps {
  budget: BudgetData;
  halted?: boolean;
}

export default function BudgetBar({ budget, halted }: BudgetBarProps) {
  const budgetCents = budget.budget_cents || 500;
  const spentCents = budget.spent_cents || 0;
  const pct = budgetCents > 0 ? Math.min(100, (spentCents / budgetCents) * 100) : 0;

  let fillBackground = 'linear-gradient(90deg, #a8e6dc, #a8e6dc)';
  if (halted || pct > 80) {
    fillBackground = 'linear-gradient(90deg, #ffe5a0, #e2605a)';
  } else if (pct > 50) {
    fillBackground = 'linear-gradient(90deg, #a8e6dc, #ffe5a0)';
  }

  const mode = budget.stripe_mode || 'mock';
  const badgeBackground =
    mode === 'mock' ? 'rgba(255,255,255,0.2)' : 'rgba(110,231,215,0.3)';
  const badgeColor = mode === 'mock' ? 'rgba(255,255,255,0.8)' : '#d4f5ef';

  return (
    <div className="budget-strip">
      <span className="budget-label">Budget</span>
      <div className="budget-track">
        <div
          className="budget-fill"
          style={{ width: `${pct}%`, background: fillBackground }}
        />
        <div className="budget-label-inner">
          {halted
            ? `$${(spentCents / 100).toFixed(2)} / $${(budgetCents / 100).toFixed(2)} (HALTED)`
            : `$${(spentCents / 100).toFixed(2)} / $${(budgetCents / 100).toFixed(2)}`}
        </div>
      </div>
      <span
        className="stripe-mode-badge"
        style={{ background: badgeBackground, color: badgeColor }}
      >
        {mode.toUpperCase()}
      </span>
    </div>
  );
}
