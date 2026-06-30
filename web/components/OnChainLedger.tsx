'use client';

import type { Attestation, EscrowState } from '@/types/messages';

interface OnChainLedgerProps {
  attestations: Attestation[];
  escrow: EscrowState | null;
  halted: {
    message: string;
    layer: number;
    spent: number;
    budget: number;
    sig: string;
  } | null;
  totalCostCents: number;
  savings: number;
}

export default function OnChainLedger({
  attestations,
  escrow,
  halted,
  totalCostCents,
  savings,
}: OnChainLedgerProps) {
  const hasContent = attestations.length > 0 || halted;
  const totalSettledTasks = attestations.reduce(
    (s, a) => s + a.task_ids.length,
    0,
  );

  return (
    <div className="ledger-panel">
      <div className="panel-title-row">
        <span className="panel-title">On-Chain Ledger</span>
        <span className="panel-count">{attestations.length} txs</span>
      </div>

      {!hasContent && (
        <div className="confirmation-empty">
          No confirmations yet.
          <br />
          Submit tasks and start the pipeline.
        </div>
      )}

      {halted && (
        <div className="halt-card">
          <div className="halt-title">⚠️ PIPELINE HALTED</div>
          <div className="halt-msg">{halted.message}</div>
          <div className="halt-meta">
            Layer: {halted.layer} | Spent: ${halted.spent.toFixed(2)} | Budget: $
            {halted.budget.toFixed(2)}
          </div>
          <div className="halt-proof">On-chain proof: {halted.sig}…</div>
        </div>
      )}

      {attestations.map((att) => (
        <div
          key={att.layer}
          className={`ledger-card${att.signature ? '' : ' pending'}`}
        >
          <div className="ledger-card-top">
            <span className="ledger-batch">Batch {att.layer}</span>
            <span className="ledger-cost">
              ${((att.cost_cents || 0) / 100).toFixed(2)}
            </span>
          </div>
          <div className="ledger-detail">
            <span className="sig">sig {(att.signature || '—').slice(0, 12)}…</span>
            <br />
            {((att.latency_ms || 0) / 1000).toFixed(1)}s ·{' '}
            {att.task_ids.length}/{att.task_ids.length} verified · confirmed
            {att.agent_ids.length > 0 && (
              <>
                <br />
                agents {att.agent_ids.join(', ')}
              </>
            )}
            {att.stripe_charge_id && (
              <>
                <br />
                stripe {att.stripe_charge_id.slice(0, 18)}…
                {att.remaining_budget_cents !== undefined && (
                  <> · remaining ${((att.remaining_budget_cents || 0) / 100).toFixed(2)}</>
                )}
              </>
            )}
          </div>
          <div className="ledger-chips">
            {att.task_ids.map((tid) => (
              <span key={tid} className="ledger-chip">
                {tid}
              </span>
            ))}
          </div>
        </div>
      ))}

      {hasContent && (
        <div className="batching-badge">
          <span className="batching-num">{savings || attestations.length}×</span>
          <span className="batching-text">
            attestations saved by batching — {totalSettledTasks} tasks settled in{' '}
            {attestations.length} transactions.
          </span>
        </div>
      )}

      {escrow && <EscrowSection escrow={escrow} />}
    </div>
  );
}

function EscrowSection({ escrow }: { escrow: EscrowState }) {
  const released = escrow.released_cents;
  const available = escrow.available_cents;
  const total = released + available;
  const pct = total > 0 ? (released / total) * 100 : 0;
  const wallets = (escrow.wallets || []).filter((w) => w.exists !== false);

  return (
    <>
      <div className="ledger-divider" />
      <div className="escrow-row">
        <span className="escrow-label">Escrow released</span>
        <span className="escrow-amount">
          ${(released / 100).toFixed(2)} / ${(total / 100).toFixed(2)}
        </span>
      </div>
      <div className="escrow-track">
        <div className="escrow-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="escrow-sub">
        <span>Released ${(released / 100).toFixed(2)}</span>
        <span>Held ${(available / 100).toFixed(2)}</span>
      </div>

      {wallets.length > 0 && (
        <div className="agent-wallets">
          {wallets
            .sort((a, b) => (a.agent_id || '').localeCompare(b.agent_id || ''))
            .map((w, i) => {
              const bal = (w.balance_cents || 0) / 100;
              const tasks = w.total_tasks || w.task_count || 0;
              const success = w.success_rate ?? '—';
              const status = w.status || 'active';
              return (
                <div key={i} className="agent-wallet">
                  <span className="aw-id">{w.agent_id || 'unknown'}</span>
                  <span className="aw-bal">${bal.toFixed(2)}</span>
                  <span className="aw-meta">
                    {tasks} tasks · {String(success)} success
                  </span>
                  <span className="aw-status">{status}</span>
                </div>
              );
            })}
        </div>
      )}

      {escrow.releases && escrow.releases.length > 0 && (
        <div className="escrow-proofs">
          <div
            style={{
              fontSize: 10,
              color: '#8b9795',
              marginBottom: 4,
              fontWeight: 600,
            }}
          >
            Batch {escrow.layer ?? ''} releases:
          </div>
          {escrow.releases.map((r, i) => {
            const ok = r.ok;
            return (
              <div key={i} className="escrow-proof-row">
                {ok ? '✅' : '❌'}{' '}
                <span style={{ color: '#1fa89d', fontWeight: 600 }}>
                  {r.agent_id}
                </span>{' '}
                →{' '}
                <span style={{ fontWeight: 700 }}>
                  ${((r.amount_cents || 0) / 100).toFixed(2)}
                </span>{' '}
                <span style={{ color: '#9aa5a3', fontSize: 10, fontFamily: 'monospace' }}>
                  {(r.transfer_id || '').slice(0, 20)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
