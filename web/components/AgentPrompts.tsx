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

export default function AgentPrompts({ agents }: AgentPromptsProps) {
  const count = agents.length;
  const cardClass = (a: AgentState) => {
    if (a.status === 'busy') return 'agent-prompt active';
    if (a.status === 'done') return 'agent-prompt submitted';
    if (a.status === 'failed') return 'agent-prompt failed';
    return 'agent-prompt';
  };

  return (
    <div className="panel-card">
      <div className="panel-header">
        <span>Agent Prompts</span>
        <span className="badge">{count} / 10</span>
      </div>
      <div className="panel-body">
        {agents.length === 0 ? (
          <div className="confirmation-empty">
            Fill in the prompt above and click Plan &amp; Submit.
            <br />
            Nemotron will decompose it into tasks with dependencies.
          </div>
        ) : (
          agents.map((a) => {
            const prog = a.progress;
            const stageColor = prog ? STAGE_COLORS[prog.stage] || '#8fa3a0' : '#8fa3a0';
            const deps =
              a.dependencies && a.dependencies.length > 0
                ? `deps: ${a.dependencies.join(', ')}`
                : 'no deps';
            return (
              <div key={a.agentId} className={cardClass(a)}>
                <div className="agent-prompt-header">
                  <span className="agent-label">
                    <span className="node-badge">NODE</span>
                    {a.taskId || a.agentId} · {a.agentId}
                  </span>
                  <span className={`agent-status ${a.status}`}>{a.status}</span>
                </div>
                {a.taskName && <div className="task-desc">{a.taskName}</div>}
                {a.taskDescription && (
                  <div className="task-sub">
                    {a.taskDescription.slice(0, 150)}
                  </div>
                )}
                <div className="deps">{deps}</div>
                {prog && (
                  <div className="task-result">
                    {prog.stage === 'model_streaming' ? (
                      <>
                        <div className="task-result-label" style={{ color: stageColor }}>
                          [{prog.time}] streaming (live)
                        </div>
                        <div
                          style={{
                            color: '#4a606c',
                            fontSize: '10px',
                            maxHeight: '150px',
                            overflowY: 'auto',
                            whiteSpace: 'pre-wrap',
                            wordWrap: 'break-word',
                          }}
                        >
                          {prog.detail}
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="task-result-label" style={{ color: stageColor }}>
                          [{prog.time}] {prog.stage}
                        </div>
                        <div style={{ color: '#5a706c' }}>{prog.detail}</div>
                      </>
                    )}
                  </div>
                )}
                {a.result && !prog && (
                  <div className="task-result">
                    <div className="task-result-label">
                      Result ({a.result.taskId}):
                    </div>
                    <div
                      style={{
                        maxHeight: '300px',
                        overflowY: 'auto',
                        whiteSpace: 'pre-wrap',
                        wordWrap: 'break-word',
                      }}
                    >
                      {a.result.summary}
                    </div>
                  </div>
                )}
                {a.verifyMethod && (
                  <div className="agent-prompt-footer">
                    <span className="verify-info">verify: {a.verifyMethod}</span>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
