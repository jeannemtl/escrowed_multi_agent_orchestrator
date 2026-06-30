'use client';

interface SummaryBarProps {
  totalTasks: number;
  totalLayers: number;
  confirmed: number;
  txs: number;
  totalCostCents: number;
  savings: number;
}

export default function SummaryBar({
  totalTasks,
  totalLayers,
  confirmed,
  txs,
  totalCostCents,
  savings,
}: SummaryBarProps) {
  return (
    <div className="summary-strip">
      <div className="stat-cell">
        <div className="val">{totalTasks}</div>
        <div className="lbl">Tasks</div>
      </div>
      <div className="stat-cell">
        <div className="val">{totalLayers}</div>
        <div className="lbl">Batches</div>
      </div>
      <div className="stat-cell">
        <div className="val">{confirmed}</div>
        <div className="lbl">Confirmed</div>
      </div>
      <div className="stat-cell">
        <div className="val">{txs}</div>
        <div className="lbl">Solana Txs</div>
      </div>
      <div className="stat-cell">
        <div className="val">${(totalCostCents / 100).toFixed(2)}</div>
        <div className="lbl">Total Cost</div>
      </div>
      <div className="stat-cell">
        <div className="val">{savings}</div>
        <div className="lbl">Txs Saved</div>
      </div>
    </div>
  );
}
