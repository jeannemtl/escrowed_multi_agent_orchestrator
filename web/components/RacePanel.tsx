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

const STEPS = [
  { key: 'attest-0', label: 'Attest Batch 0', num: 1 },
  { key: 'attest-1', label: 'Attest Batch 1', num: 2 },
  { key: 'confirm', label: 'Confirmation', num: 3 },
  { key: 'escrow', label: 'Escrow Payment', num: 4 },
];

export default function RacePanel({
  race,
  open,
  onClose,
  planning = false,
  plannerStatus = '',
  plannerStatusColor = '#ffe5a0',
}: RacePanelProps) {
  // When in the race planning phase (before comparison_start), show a centered
  // loading state with the planning message instead of task cards.
  const showPlanning = planning && !race;

  return (
    <>
      <div
        className={`race-backdrop ${open ? 'active' : ''}`}
        onClick={onClose}
        style={{ display: open ? 'block' : 'none' }}
      />
      <div className={`race-overlay ${open ? 'active' : ''}`}>
        <div className="race-header">
          <h1>🏁 Live Race: Cerebras vs GPU</h1>
          <button className="race-close" onClick={onClose} title="Hide panel (race continues)">
            — Hide
          </button>
        </div>

        {showPlanning ? (
          <div className="race-planning-state">
            <div className="race-planning-spinner" aria-hidden />
            <div
              className="race-planning-text"
              style={{ color: plannerStatusColor }}
            >
              {plannerStatus || 'Analyzing image with vision model, then decomposing into race tasks...'}
            </div>
            <div className="race-planning-sub">
              Preparing race tasks · Cerebras vs GPU
            </div>
          </div>
        ) : (
          <>
            <div className="race-prompt-display">
              {race ? race.promptDisplay : ''}
            </div>

            {race && race.decomposition.length > 0 && (
              <div className="race-decomp" style={{ display: 'flex' }}>
                <span className="race-decomp-label">Decomposition:</span>
                {race.decomposition.map((layer, i) => (
                  <div key={layer.layer} style={{ display: 'flex', alignItems: 'center' }}>
                    {i > 0 && <span className="race-decomp-arrow">&gt;&gt;&gt;</span>}
                    <div className="race-decomp-layer">
                      <span className="race-decomp-layer-num">L{layer.layer}</span>
                      <span className="race-decomp-layer-name">{layer.name}</span>
                      <span className="race-decomp-layer-tasks">
                        {layer.tasks?.length || 0} tasks
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Steppers */}
            {(['cerebras', 'gpu'] as const).map((side) => (
              <div key={side} className="race-stepper">
                {STEPS.map((step, i) => {
                  const state = race?.steps[side][step.key] || 'pending';
                  return (
                    <div key={step.key} style={{ display: 'flex', alignItems: 'center' }}>
                      {i > 0 && <span className="race-step-arrow">&gt;&gt;&gt;</span>}
                      <div className={`race-step ${state}`}>
                        <div className="race-step-circle">
                          {state === 'done' ? '✓' : step.num}
                        </div>
                        <span className="race-step-label">{step.label}</span>
                      </div>
                    </div>
                  );
                })}
                <span className={`race-step-side-label ${side}`}>
                  {side.toUpperCase()}
                </span>
              </div>
            ))}

            <div className="race-body">
              {race &&
                (['cerebras', 'gpu'] as const).map((side) => (
                  <RaceSide
                    key={side}
                    side={side}
                    race={race}
                    layers={race.layers}
                  />
                ))}
            </div>

            {race?.finish && (
              <div className="race-finish-banner">
                <div className={`race-speedup ${race.finish.winner}`}>
                  {race.finish.speedup > 1
                    ? `CEREBRAS ${race.finish.speedup.toFixed(1)}x FASTER`
                    : race.finish.speedup > 0
                      ? `GPU ${(1 / race.finish.speedup).toFixed(1)}x FASTER`
                      : 'RACE COMPLETE'}
                </div>
                <div className="race-finish-detail">
                  Cerebras: <b style={{ color: '#d4f5ef' }}>{race.finish.cerebrasTime.toFixed(1)}s</b>
                  &nbsp;|&nbsp; GPU: <b style={{ color: '#ffd0cc' }}>{race.finish.gpuTime.toFixed(1)}s</b>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}

function RaceSide({
  side,
  race,
  layers,
}: {
  side: 'cerebras' | 'gpu';
  race: RaceState;
  layers: LayerInfo[];
}) {
  const sideState = race[side];
  const timerText = (
    sideState.done ? sideState.totalTime : sideState.elapsed
  ).toFixed(1) + 's';
  const model = side === 'cerebras' ? race.cerebrasModel : race.glmModel;
  const totalBudget = race.budget || 500;
  const remaining = totalBudget - sideState.released;
  const releasedPct =
    totalBudget > 0 ? Math.max(0, ((totalBudget - remaining) / totalBudget) * 100) : 0;

  return (
    <div className={`race-side ${side}`}>
      <div className="race-side-header">
        <div>
          <div>{side.toUpperCase()}</div>
          <div className="race-model-name">{model}</div>
          {sideState.finishStamp && (
            <div className="race-finish-stamp">
              DONE {sideState.finishStamp.time.toFixed(1)}s (
              {sideState.finishStamp.successful}/{sideState.finishStamp.totalTasks} ok)
            </div>
          )}
        </div>
        <div className="race-timer">{timerText}</div>
      </div>
      <div className="race-tasks">
        {layers.map((layer) => (
          <RaceLayerGroup
            key={layer.layer}
            side={side}
            layer={layer}
            race={race}
          />
        ))}
      </div>
      <div className="race-escrow">
        <div className="race-escrow-label">Escrow &amp; Agent Wallet</div>
        <div className="race-escrow-bar">
          <div className="race-escrow-fill" style={{ width: `${releasedPct}%` }} />
        </div>
        <div className="race-escrow-detail">
          <span>Released: ${(sideState.released / 100).toFixed(2)}</span>
          <span>Budget: ${(totalBudget / 100).toFixed(2)}</span>
        </div>
        <div className="race-escrow-wallet">
          Agent wallet: ${(sideState.wallet / 100).toFixed(2)}
        </div>
      </div>
    </div>
  );
}

function RaceLayerGroup({
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

  return (
    <div className="race-layer-group">
      <div className="race-layer-header">
        <span>
          Batch {layer.layer}: {layer.name || ''}
        </span>
        <span className={`race-layer-status ${attested ? 'attested' : 'dispatching'}`}>
          {attested
            ? `attested (${layerState?.verifiedCount}/${layerState?.taskCount})`
            : `${layerState?.status || 'dispatching'} (${layerState?.taskCount || tasks.length} tasks)`}
        </span>
      </div>
      {tasks.map((task) => {
        const tid = task.id;
        const taskState = race[side].tasks[tid];
        let cardClass = 'race-task-card';
        let statusClass = 'race-task-status pending';
        let statusText = 'pending';
        if (taskState?.done) {
          if (taskState.error) {
            cardClass = 'race-task-card error';
            statusClass = 'race-task-status error';
            statusText = 'error';
          } else {
            cardClass = 'race-task-card done';
            statusClass = 'race-task-status done';
            statusText = taskState.ok === false ? 'failed' : 'done';
          }
        } else if (taskState) {
          cardClass = 'race-task-card active';
          statusClass = 'race-task-status running';
          statusText = 'running...';
        }
        const latencyText = taskState?.latency_s
          ? taskState.latency_s.toFixed(1) + 's'
          : '';
        const latencyColor = side === 'cerebras' ? '#1fa89d' : '#e2605a';
        return (
          <div key={tid} className={cardClass}>
            <div className="race-task-name">
              {task.name}
              <span className="race-task-latency" style={{ color: latencyText ? latencyColor : 'transparent' }}>
                {latencyText}
              </span>
            </div>
            <span className={statusClass}>{statusText}</span>
          </div>
        );
      })}
      {attested && (
        <div className="race-attest">
          <span className="race-attest-icon">✅</span>
          <span className="race-attest-detail">
            Batch {layer.layer} attested · {layerState?.verifiedCount}/
            {layerState?.taskCount} verified ·{' '}
            {((layerState?.latencyMs || 0) / 1000).toFixed(1)}s · $
            {((layerState?.costCents || 0) / 100).toFixed(2)}
          </span>
          <span className="race-attest-sig">
            {(layerState?.signature || '').slice(0, 20)}...
          </span>
        </div>
      )}
    </div>
  );
}
