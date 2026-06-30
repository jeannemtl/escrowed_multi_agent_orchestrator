'use client';

import type { LayerInfo, RaceState } from '@/types/messages';

interface RacePanelProps {
  race: RaceState | null;
  open: boolean;
  onClose: () => void;
  planning?: boolean;
  plannerStatus?: string;
  plannerStatusColor?: string;
}

export default function RacePanel({
  race,
  open,
  onClose,
  planning = false,
  plannerStatus = '',
  plannerStatusColor = '#ffe5a0',
}: RacePanelProps) {
  const showPlanning = planning && !race;

  return (
    <div className={`race-overlay${open ? '' : ' hidden'}`}>
      <div className="race-topbar">
        <div className="race-topbar-left">
          <span className="race-title">Live Race</span>
          <span className="race-subtitle">Cerebras × GPU</span>
        </div>
        <div className="race-topbar-right">
          <button className="race-btn-close" onClick={onClose} title="Close race panel">
            Close ✕
          </button>
        </div>
      </div>

      {showPlanning ? (
        <div className="race-planning-state">
          <div className="race-planning-spinner" aria-hidden />
          <div className="race-planning-text" style={{ color: plannerStatusColor }}>
            {plannerStatus ||
              'Analyzing image with vision model, then decomposing into race tasks…'}
          </div>
          <div className="race-planning-sub">
            Preparing race tasks · Cerebras vs GPU
          </div>
        </div>
      ) : (
        <>
          <div className="race-infobar">
            <span className="race-info-prompt">
              {race ? race.promptDisplay : ''}
            </span>
            <div className="race-info-spacer" />
            {race &&
              race.decomposition.length > 0 &&
              race.decomposition.map((layer, i) => (
                <div
                  key={layer.layer}
                  style={{ display: 'flex', alignItems: 'center' }}
                >
                  {i > 0 && <span className="race-info-arrow">›››</span>}
                  <span className="race-info-chip">
                    L{layer.layer} · {layer.name || 'Batch'} ·{' '}
                    {layer.tasks?.length || layer.task_ids?.length || 0}
                  </span>
                </div>
              ))}
          </div>

          <div className="race-grid">
            {race &&
              (['cerebras', 'gpu'] as const).map((side) => (
                <RaceSide key={side} side={side} race={race} />
              ))}
          </div>

          {race?.finish && (
            <div className="race-finish-banner">
              <div className="race-finish-headline">
                <b>
                  {race.finish.winner === 'cerebras' ? 'CEREBRAS' : 'GPU'}
                </b>{' '}
                {race.finish.speedup >= 1
                  ? `${race.finish.speedup.toFixed(1)}× faster`
                  : `${(1 / race.finish.speedup).toFixed(1)}× faster`}
              </div>
              <div className="race-finish-detail">
                Cerebras {race.finish.cerebrasTime.toFixed(1)}s (
                {race.finish.cerebrasSuccessful}/{race.finish.cerebrasTotal}) · GPU{' '}
                {race.finish.gpuTime.toFixed(1)}s ({race.finish.gpuSuccessful}/
                {race.finish.gpuTotal})
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function RaceSide({
  side,
  race,
}: {
  side: 'cerebras' | 'gpu';
  race: RaceState;
}) {
  const s = race[side];
  const model = side === 'cerebras' ? race.cerebrasModel : race.glmModel;
  // Compute elapsed from startTime so it's never stale on re-render
  const liveElapsed = (Date.now() - race.startTime) / 1000;
  const timer = (s.done ? s.totalTime : (s.elapsed || liveElapsed)).toFixed(1) + 's';
  const totalBudget = race.budget || 500;
  const releasedPct =
    totalBudget > 0
      ? Math.max(0, Math.min(100, (s.released / totalBudget) * 100))
      : 0;
  const finished = !!race.finish;
  const isWinner = race.finish?.winner === side;

  return (
    <div className={`race-side ${side}`}>
      {finished && isWinner && <div className="race-side-glow" />}

      <div className="race-side-header">
        <div>
          <div className="race-side-title-row">
            <span className="race-side-name">{side.toUpperCase()}</span>
            {!finished && <span className="race-live-dot" />}
            {finished && (
              <span className="race-done-stamp">
                DONE · {s.finishStamp ? `${s.finishStamp.successful}/${s.finishStamp.totalTasks} OK` : 'OK'}
              </span>
            )}
          </div>
          <div className="race-model-name">{model}</div>
        </div>
        <span className="race-timer">{timer}</span>
      </div>

      <div className="race-tasks">
        {race.layers.map((layer) => (
          <RaceBatchBlock key={layer.layer} side={side} layer={layer} race={race} />
        ))}
      </div>

      <div className="race-escrow-box">
        <div className="race-escrow-line">
          <span>Escrow &amp; agent wallet</span>
          <span>Budget ${(totalBudget / 100).toFixed(2)}</span>
        </div>
        <div className="race-escrow-track">
          <div className="race-escrow-fill" style={{ width: `${releasedPct}%` }} />
        </div>
        <div className="race-escrow-sub">
          <span className="released">Released ${(s.released / 100).toFixed(2)}</span>
          <span className="wallet">Agent wallet ${(s.wallet / 100).toFixed(2)}</span>
        </div>
      </div>
    </div>
  );
}

function RaceBatchBlock({
  side,
  layer,
  race,
}: {
  side: 'cerebras' | 'gpu';
  layer: LayerInfo;
  race: RaceState;
}) {
  const layerState = race[side].layers[layer.layer];
  const attested = layerState?.attested;
  const tasks = layer.tasks || [];
  const taskIds = layer.task_ids || tasks.map((t) => t.id);
  const taskCount = layerState?.taskCount || taskIds.length;
  const verified = layerState?.verifiedCount ?? 0;

  return (
    <div className="race-batch-block">
      <div className="race-batch-header">
        <span className="race-batch-title">
          Batch {layer.layer} · {layer.name || ''}
        </span>
        <span className="race-batch-badge">
          {attested
            ? `attested ${verified}/${taskCount}`
            : `${layerState?.status || 'dispatching'} ${taskCount}`}
        </span>
      </div>

      {tasks.map((task) => {
        const tid = task.id;
        const ts = race[side].tasks[tid];
        let cardClass = 'race-task-card';
        if (ts?.done) {
          cardClass = ts.error
            ? 'race-task-card error'
            : 'race-task-card done';
        } else if (ts) {
          cardClass = 'race-task-card active';
        }
        const lat = ts?.latency_s ? `${ts.latency_s.toFixed(1)}s` : '—';
        return (
          <div key={tid} className={cardClass}>
            <div className="race-task-line">
              <span className="race-task-name">{task.name}</span>
              <span className="race-task-lat">{lat}</span>
            </div>
            {ts?.error && <div className="race-task-out">{ts.error}</div>}
          </div>
        );
      })}

      {attested && (
        <div className="race-attest">
          <span className="race-attest-icon">✓</span>
          Batch {layer.layer} attested · {verified}/{taskCount} verified ·{' '}
          {((layerState?.latencyMs || 0) / 1000).toFixed(1)}s · $
          {((layerState?.costCents || 0) / 100).toFixed(2)} ·{' '}
          <span className="race-attest-sig">
            {(layerState?.signature || '').slice(0, 12)}…
          </span>
        </div>
      )}
    </div>
  );
}
