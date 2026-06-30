'use client';

import type { LayerInfo, PlannedTask, TaskState } from '@/types/messages';

interface TaskDAGProps {
  layers: LayerInfo[];
  taskMap: Record<string, TaskState>;
  plannedTasks: PlannedTask[];
}

const STAGE_COLORS: Record<string, string> = {
  received: '#1fa89d',
  calling_model: '#e09a2a',
  model_calling: '#e09a2a',
  model_streaming: '#8b4fb0',
  model_retry: '#d94f4f',
  model_done: '#2d9d6a',
  completed: '#2d9d6a',
};

export default function TaskDAG({ layers, taskMap, plannedTasks }: TaskDAGProps) {
  const totalTasks = layers.reduce(
    (sum, l) => sum + (l.task_ids?.length || l.tasks?.length || 0),
    0,
  );
  const totalLayers = layers.length;

  return (
    <div className="panel-card">
      <div className="panel-header">
        <span>Task DAG</span>
        <span className="badge">
          {totalTasks === 0 ? 'No DAG' : `${totalTasks} tasks / ${totalLayers} layers`}
        </span>
      </div>
      <div className="panel-body">
        {layers.length === 0 ? (
          <div className="confirmation-empty">
            No tasks loaded.
            <br />
            Fill in agent prompts and click Submit All.
          </div>
        ) : (
          <div className="task-queue-inner">
            {layers.map((layer) => {
              const taskIds = layer.task_ids || [];
              const taskNames = layer.task_names || [];
              const agents = layer.agents || [];
              const verifyMethods = layer.verify_methods || [];
              return (
                <div key={layer.layer} className="layer-group">
                  <div className="layer-header">
                    <span>Batch {layer.layer} ({taskIds.length} tasks)</span>
                    <span className="layer-status pending">pending</span>
                  </div>
                  <div className="task-grid">
                    {taskIds.map((tid, i) => {
                      const task = taskMap[tid];
                      const status = task?.status || 'pending';
                      const planned = plannedTasks.find((t) => t.id === tid);
                      const deps =
                        planned?.dependencies && planned.dependencies.length > 0
                          ? planned.dependencies.join(', ')
                          : null;
                      const prog = task?.progress;
                      const stageColor = prog
                        ? STAGE_COLORS[prog.stage] || '#8fa3a0'
                        : '#8fa3a0';
                      return (
                        <div key={tid} className={`task-card ${status}`}>
                          <span className="task-id">{tid}</span>
                          <span className="task-agent">[{agents[i] || '?'}]</span>
                          {verifyMethods[i] && (
                            <span className="task-verify">{verifyMethods[i]}</span>
                          )}
                          <span className="task-name">{taskNames[i] || ''}</span>
                          <div
                            style={{
                              marginTop: '4px',
                              fontSize: '10px',
                              color: deps ? '#e09a2a' : '#2d9d6a',
                            }}
                          >
                            {deps ? `blocked on: ${deps}` : 'ready (no deps)'}
                          </div>
                          {prog && (
                            <div className="task-progress">
                              <span style={{ color: stageColor }}>
                                [{prog.time}]{' '}
                                {prog.stage === 'model_streaming'
                                  ? 'streaming'
                                  : prog.stage}
                              </span>
                              <span style={{ color: '#5a706c', fontSize: '9px' }}>
                                {prog.stage === 'model_streaming'
                                  ? ': ' + prog.detail.slice(0, 300)
                                  : ': ' + prog.detail.slice(0, 120)}
                              </span>
                            </div>
                          )}
                          {task?.resultSummary && (
                            <div className="task-result-inline">
                              <span style={{ color: '#2d9d6a' }}>Result:</span>{' '}
                              {task.resultSummary.slice(0, 200)}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            {totalLayers > 0 && (
              <div className="synthesis-row">
                <div className="synth-label">Synthesis</div>
                <div style={{ fontSize: '11px', color: '#4a605c' }}>
                  Aggregated outputs across {totalLayers} batches will be synthesized
                  into a final answer.
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
