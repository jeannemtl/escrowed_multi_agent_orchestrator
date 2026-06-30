'use client';

import type { LayerInfo, PlannedTask, TaskState } from '@/types/messages';

interface TaskDAGProps {
  layers: LayerInfo[];
  taskMap: Record<string, TaskState>;
  plannedTasks: PlannedTask[];
}

interface FlatTask {
  tid: string;
  name: string;
  agent: string;
  verify: string;
  status: TaskState['status'];
  progress?: TaskState['progress'];
  resultSummary?: string;
  outputPreview?: string;
  outputLength?: number;
  latency?: number;
  deps?: string | null;
  layerIdx: number;
  layerName: string;
}

function heartCount(len: number | undefined): number {
  if (!len) return 0;
  return Math.max(1, Math.round(len / 400));
}

function quoteCount(len: number | undefined): number {
  if (!len) return 0;
  return Math.max(1, Math.round(len / 300));
}

function statusWord(s: TaskState['status']): string {
  return s === 'confirmed' ? 'confirmed' : s === 'verified' ? 'verified' : s;
}

export default function TaskDAG({ layers, taskMap, plannedTasks }: TaskDAGProps) {
  const totalTasks = layers.reduce(
    (sum, l) => sum + (l.task_ids?.length || l.tasks?.length || 0),
    0,
  );
  const totalLayers = layers.length;

  if (layers.length === 0) {
    return (
      <div className="dag-panel">
        <div className="dag-title-row">
          <span className="dag-title">Task DAG</span>
          <span className="dag-sub">No tasks</span>
        </div>
        <div className="confirmation-empty">
          No tasks loaded.
          <br />
          Fill in a prompt and click Plan &amp; Submit.
        </div>
      </div>
    );
  }

  // Flatten all layer tasks into a single ordered list.
  const flat: FlatTask[] = [];
  layers.forEach((layer) => {
    const ids = layer.task_ids || (layer.tasks || []).map((t) => t.id);
    const names = layer.task_names || (layer.tasks || []).map((t) => t.name);
    const agents = layer.agents || [];
    const verifies = layer.verify_methods || [];
    ids.forEach((tid, i) => {
      const task = taskMap[tid];
      const planned = plannedTasks.find((t) => t.id === tid);
      flat.push({
        tid,
        name: names[i] || planned?.name || tid,
        agent: agents[i] || task?.agent || '—',
        verify: verifies[i] || planned?.verify_method || '',
        status: task?.status || 'pending',
        progress: task?.progress,
        resultSummary: task?.resultSummary,
        outputPreview: task?.outputPreview,
        outputLength: task?.outputLength,
        latency: task?.latency_s,
        deps:
          planned?.dependencies && planned.dependencies.length > 0
            ? planned.dependencies.join(', ')
            : null,
        layerIdx: layer.layer,
        layerName: layer.name || `Batch ${layer.layer}`,
      });
    });
  });

  // The final layer (if more than one) is the synthesis batch.
  const hasSynthesis = totalLayers > 1;
  const gridTasks = hasSynthesis
    ? flat.filter((t) => t.layerIdx !== layers[layers.length - 1].layer)
    : flat;
  const synthTasks = hasSynthesis
    ? flat.filter((t) => t.layerIdx === layers[layers.length - 1].layer)
    : [];

  const firstLayerName = layers[0].name || `Batch ${layers[0].layer}`;
  const subtitle = `${firstLayerName} · ${totalTasks} tasks`;

  const lastRow = Math.floor((gridTasks.length - 1) / 2);

  return (
    <div className="dag-panel">
      <div className="dag-title-row">
        <span className="dag-title">Task DAG</span>
        <span className="dag-sub">{subtitle}</span>
      </div>

      <div className="dag-grid">
        {gridTasks.map((t, i) => {
          const col = i % 2;
          const row = Math.floor(i / 2);
          const evenRow = row % 2 === 0;
          const isLast = row === lastRow;
          const cellClass =
            `dag-cell${col === 0 ? ' br' : ''}${!isLast ? ' bb' : ''}`;
          return (
            <div key={t.tid} className={cellClass}>
              {evenRow ? (
                <>
                  <TaskHead t={t} />
                  <OutputArea t={t} pointerDown />
                </>
              ) : (
                <>
                  <OutputArea t={t} />
                  <TaskHead t={t} pointerUp />
                </>
              )}
            </div>
          );
        })}
      </div>

      {synthTasks.length > 0 && (
        <div className="synth-row">
          <div className="node-badge-lg">t{gridTasks.length}</div>
          <div className="synth-body">
            <div className="synth-name">
              {synthTasks[0].name}
            </div>
            <div className="synth-meta">
              {synthTasks[0].layerName} · {synthTasks[0].agent}
              {synthTasks[0].latency ? ` · ${synthTasks[0].latency.toFixed(1)}s` : ''}
              {synthTasks[0].deps ? ` · deps ${synthTasks[0].deps}` : ''}
              {synthTasks[0].resultSummary
                ? ` — ${synthTasks[0].resultSummary.slice(0, 120)}`
                : ''}
            </div>
          </div>
          <div className="task-hearts">
            <span className="task-heart" style={{ fontSize: 22 }}>♥</span>
            <span className="task-heart-count">
              {heartCount(synthTasks[0].outputLength)}
            </span>
          </div>
        </div>
      )}

      {synthTasks.length > 0 && (
        <div className="synth-output">
          <div className="task-output-area">
            <div className="task-quote-badge">❝ {quoteCount(synthTasks[0].outputLength)}</div>
            <div className="task-output-inner padtop">
              <div className="task-output-label">
                OUTPUT · {(synthTasks[0].outputLength || (synthTasks[0].outputPreview || synthTasks[0].resultSummary || '').length).toLocaleString()} CHARS
              </div>
              <div className={`task-output-text${(synthTasks[0].outputPreview || synthTasks[0].resultSummary) ? '' : ' muted'}`}>
                {synthTasks[0].outputPreview || synthTasks[0].resultSummary || 'Awaiting output…'}
              </div>
            </div>
            <div className="task-output-fade" />
          </div>
        </div>
      )}
    </div>
  );
}

function TaskHead({ t, pointerUp }: { t: FlatTask; pointerUp?: boolean }) {
  const prog = t.progress;
  const lat = t.latency ? `${t.latency.toFixed(1)}s` : '—';
  return (
    <div className="task-head" style={{ position: pointerUp ? 'relative' : undefined }}>
      {pointerUp && <div className="task-output-pointer up" />}
      <div className="task-head-left">
        <div className="node-badge-md">{t.tid}</div>
        <div className="task-name-block">
          <div className="task-name">{t.name}</div>
          <div className="task-sub">
            {t.agent} · {statusWord(t.status)} · {lat}
            {prog && (
              <span className="stage">
                {' '}
                · [{prog.time}]{' '}
                {prog.stage === 'model_streaming' ? 'streaming' : prog.stage}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="task-hearts">
        <span className="task-heart">♥</span>
        <span className="task-heart-count">{heartCount(t.outputLength)}</span>
      </div>
    </div>
  );
}

function OutputArea({
  t,
  pointerDown,
}: {
  t: FlatTask;
  pointerDown?: boolean;
}) {
  const output = t.outputPreview || t.resultSummary || '';
  const len = t.outputLength || (output ? output.length : 0);
  return (
    <div className="task-output-area">
      {pointerDown && <div className="task-output-pointer down" />}
      <div className="task-quote-badge">❝ {quoteCount(t.outputLength)}</div>
      <div className={`task-output-inner${pointerDown ? ' padtop' : ''}`}>
        <div className="task-output-label">
          OUTPUT · {len.toLocaleString()} CHARS
        </div>
        <div className={`task-output-text${output ? '' : ' muted'}`}>
          {output || 'Awaiting output…'}
        </div>
      </div>
      <div className="task-output-fade" />
    </div>
  );
}
