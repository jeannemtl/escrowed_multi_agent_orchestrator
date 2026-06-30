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
  return (
    <div className="panel-card">
      <div className="panel-header">
        <span>On-Chain Ledger</span>
        <span className="badge">{attestations.length} txs</span>
      </div>
      <div className="panel-body">
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
            <div className="halt-proof">
              On-chain proof: {halted.sig}...
            </div>
          </div>
        )}

        {attestations.map((att) => (
          <div key={att.layer} className="confirmation-card">
            <div className="confirmation-header">
              <span className="confirmation-layer">Batch {att.layer}</span>
              <span className="confirmation-sig">
                {(att.signature || '').slice(0, 24)}...
              </span>
            </div>
            <div className="confirmation-details">
              <span className="label">Tasks:</span> {att.task_ids.length} &nbsp;
              <span className="label">Agents:</span> {att.agent_ids.join(', ')} &nbsp;
              <span className="label">Latency:</span>{' '}
              {att.latency_ms?.toFixed(0) || 0}ms &nbsp;
              <span className="label">Cost:</span> $
              {((att.cost_cents || 0) / 100).toFixed(2)}
            </div>
            {att.stripe_charge_id && (
              <div style={{ marginTop: '5px', fontSize: '10px' }}>
                <span style={{ color: '#2d9d6a' }}>Stripe:</span>{' '}
                <span style={{ color: '#5a706c' }}>{att.stripe_charge_id}</span> &nbsp;
                <span style={{ color: '#2d9d6a' }}>Remaining:</span>{' '}
                <span style={{ color: '#5a706c' }}>
                  ${((att.remaining_budget_cents || 0) / 100).toFixed(2)}
                </span>
              </div>
            )}
            <div className="confirmation-tasks">
              {att.task_ids.map((tid) => (
                <span key={tid} className="confirmation-task">
                  {tid}
                </span>
              ))}
            </div>
          </div>
        ))}

        {hasContent && (
          <>
            <div className="cost-display">
              ${(totalCostCents / 100).toFixed(2)}
            </div>
            <div className="savings-badge">
              {savings} txs saved via batching
            </div>
          </>
        )}

        {escrow && <EscrowBar escrow={escrow} />}
      </div>
    </div>
  );
}

function EscrowBar({ escrow }: { escrow: EscrowState }) {
  const released = escrow.released_cents;
  const available = escrow.available_cents;
  const total = released + available;
  const pct = total > 0 ? (available / total) * 100 : 100;
  const wallets = escrow.wallets || [];

  return (
    <>
      <div className="escrow-balance-bar">
        <div className="escrow-row">
          <span className="escrow-lbl">Escrow Balance</span>
          <span className="escrow-balance-text">
            ${(available / 100).toFixed(2)}
          </span>
        </div>
        <div className="escrow-track">
          <div className="escrow-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="escrow-sub">
          <span>Released: ${(released / 100).toFixed(2)}</span>
          <span>Held: ${(available / 100).toFixed(2)}</span>
        </div>
      </div>
      {wallets.length > 0 && (
        <div className="agent-wallets">
          {wallets
            .filter((w) => w.exists !== false)
            .sort((a, b) => (a.agent_id || '').localeCompare(b.agent_id || ''))
            .map((w, i) => {
              const bal = (w.balance_cents || 0) / 100;
              const tasks = w.total_tasks || w.task_count || 0;
              const success = w.success_rate || '—';
              const status = w.status || 'active';
              const statusColor =
                status === 'promoted'
                  ? '#2d9d6a'
                  : status === 'halted'
                    ? '#d94f4f'
                    : '#1fa89d';
              return (
                <div key={i} className="agent-wallet">
                  <span className="aw-id">{w.agent_id || 'unknown'}</span>
                  <span className="aw-bal">${bal.toFixed(2)}</span>
                  <span className="aw-meta">
                    {tasks} tasks · {String(success)} success
                  </span>
                  <span className="aw-status" style={{ color: statusColor }}>
                    {status}
                  </span>
                </div>
              );
            })}
        </div>
      )}
      {escrow.releases && escrow.releases.length > 0 && (
        <div className="escrow-proofs">
          <div
            style={{
              fontSize: '10px',
              color: '#8fa3a0',
              marginBottom: '4px',
              fontWeight: 600,
            }}
          >
            Batch {escrow.layer} releases:
          </div>
          {escrow.releases.map((r, i) => {
            const ok = r.ok;
            const color = ok ? '#2d9d6a' : '#d94f4f';
            const icon = ok ? '✅' : '❌';
            return (
              <div key={i} className="escrow-proof-row">
                {icon}{' '}
                <span style={{ color: '#1fa89d', fontWeight: 600 }}>
                  {r.agent_id}
                </span>{' '}
                →{' '}
                <span style={{ color, fontWeight: 700 }}>
                  ${((r.amount_cents || 0) / 100).toFixed(2)}
                </span>{' '}
                <span
                  style={{
                    color: '#9bb5b1',
                    fontSize: '10px',
                    fontFamily: 'monospace',
                  }}
                >
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
