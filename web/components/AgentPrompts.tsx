'use client';

import type { AgentState } from '@/types/messages';

interface AgentPromptsProps {
  agents: AgentState[];
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

function statusLabel(s: AgentState['status']): string {
  if (s === 'done') return 'DONE';
  if (s === 'busy') return 'BUSY';
  if (s === 'failed') return 'FAILED';
  return 'IDLE';
}

export default function AgentPrompts({ agents }: AgentPromptsProps) {
  const count = agents.length;

  return (
    <div className="agent-panel">
      <div className="panel-title-row">
        <span className="panel-title">Agent Prompts</span>
        <span className="panel-count">{count} / 10</span>
      </div>

      {count === 0 ? (
        <div className="confirmation-empty">
          Fill in the prompt above and click Plan &amp; Submit.
          <br />
          Nemotron will decompose it into tasks with dependencies.
        </div>
      ) : (
        agents.map((a, i) => {
          const prog = a.progress;
          const stageColor = prog ? STAGE_COLORS[prog.stage] || '#8fa3a0' : '#8fa3a0';
          const deps =
            a.dependencies && a.dependencies.length > 0
              ? `deps ${a.dependencies.join(', ')}`
              : 'no deps';
          const code = `A-${String(i + 1).padStart(2, '0')}`;
          return (
            <div key={a.agentId} className="agent-row">
              <div className="agent-row-top">
                <div className="agent-id-group">
                  <div className="node-badge-sm">NODE</div>
                  <span className="agent-id">{a.agentId}</span>
                  <span className="agent-code">{code}</span>
                </div>
                <span className="agent-status-pill">
                  <span className={`agent-status-dot ${a.status}`} />
                  {statusLabel(a.status)}
                </span>
              </div>

              <div className="agent-task">
                {a.taskDescription
                  ? a.taskDescription.slice(0, 160)
                  : a.taskName || a.taskId || '—'}
              </div>

              {a.verifyMethod && (
                <div className="agent-verify">
                  verify {a.verifyMethod} · {deps}
                </div>
              )}

              {prog && (
                <div className="agent-stream">
                  <div className="agent-stream-label" style={{ color: stageColor }}>
                    [{prog.time}] {prog.stage === 'model_streaming' ? 'streaming (live)' : prog.stage}
                  </div>
                  {prog.detail}
                </div>
              )}

              {a.result && !prog && (
                <div className="agent-stream">
                  <div className="agent-stream-label" style={{ color: '#2d9d6a' }}>
                    Result ({a.result.taskId})
                  </div>
                  {a.result.summary}
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
