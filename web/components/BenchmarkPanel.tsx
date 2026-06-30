'use client';

interface BenchmarkPanelProps {
  benchmark: {
    cerebrasTotal: number;
    glmTotal: number;
    speedup: number;
    tasks: { name: string; cMs: number; gMs: number; speedup: number }[];
  } | null;
}

export default function BenchmarkPanel({ benchmark }: BenchmarkPanelProps) {
  if (!benchmark) return null;
  const speedup = benchmark.speedup;
  const speedupColor =
    speedup > 10 ? '#2d9d6a' : speedup > 3 ? '#e09a2a' : '#d94f4f';

  return (
    <div className="panel-card">
      <div className="panel-header">
        <span>⚡ Speed Benchmark</span>
        <span className="badge">CEREBRAS vs GPU</span>
      </div>
      <div className="panel-body">
        <div className="bench-summary">
          <div className="bench-title">Speed Comparison</div>
          <div className="bench-total-speedup" style={{ color: speedupColor }}>
            {speedup.toFixed(1)}x faster
          </div>
          <div className="bench-sub">Cerebras Gemma 4 31B vs GPU GLM-5.2</div>
        </div>
        <div className="bench-table">
          <span className="bench-head">Task</span>
          <span className="bench-head" style={{ textAlign: 'right' }}>
            Cerebras
          </span>
          <span className="bench-head" style={{ textAlign: 'right' }}>
            GPU GLM
          </span>
          <span className="bench-head" style={{ textAlign: 'right' }}>
            Speedup
          </span>
        </div>
        {benchmark.tasks.map((t, i) => {
          const tSpeedup = t.speedup;
          const sColor =
            tSpeedup > 10 ? '#2d9d6a' : tSpeedup > 3 ? '#e09a2a' : '#d94f4f';
          return (
            <div key={i} className="bench-row">
              <span
                style={{ fontSize: '11px', color: '#1f2d2c', fontWeight: 500 }}
              >
                {t.name}
              </span>
              <span
                style={{
                  textAlign: 'right',
                  fontFamily: "'Jost', sans-serif",
                  fontSize: '12px',
                  fontWeight: 700,
                  color: '#1fa89d',
                }}
              >
                {(t.cMs / 1000).toFixed(1)}s
              </span>
              <span
                style={{
                  textAlign: 'right',
                  fontFamily: "'Jost', sans-serif",
                  fontSize: '12px',
                  color: '#e2605a',
                }}
              >
                {(t.gMs / 1000).toFixed(1)}s
              </span>
              <span
                style={{
                  textAlign: 'right',
                  fontFamily: "'Jost', sans-serif",
                  fontSize: '12px',
                  fontWeight: 700,
                  color: sColor,
                }}
              >
                {tSpeedup.toFixed(1)}x
              </span>
            </div>
          );
        })}
        <div className="bench-total-row">
          <span
            style={{
              fontFamily: "'Jost', sans-serif",
              fontSize: '11px',
              fontWeight: 700,
              color: '#e09a2a',
            }}
          >
            TOTAL
          </span>
          <span
            style={{
              textAlign: 'right',
              fontFamily: "'Jost', sans-serif",
              fontSize: '13px',
              fontWeight: 700,
              color: '#1fa89d',
            }}
          >
            {benchmark.cerebrasTotal.toFixed(1)}s
          </span>
          <span
            style={{
              textAlign: 'right',
              fontFamily: "'Jost', sans-serif",
              fontSize: '13px',
              fontWeight: 700,
              color: '#e2605a',
            }}
          >
            {benchmark.glmTotal.toFixed(1)}s
          </span>
          <span
            style={{
              textAlign: 'right',
              fontFamily: "'Jost', sans-serif",
              fontSize: '13px',
              fontWeight: 700,
              color: '#2d9d6a',
            }}
          >
            {speedup.toFixed(1)}x
          </span>
        </div>
      </div>
    </div>
  );
}
