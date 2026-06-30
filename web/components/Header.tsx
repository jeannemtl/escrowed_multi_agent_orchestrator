'use client';

import type { SpeedupBadge } from '@/types/messages';

interface HeaderProps {
  connected: boolean;
  speedupBadge: SpeedupBadge | null;
  onConnect: () => void;
  onStart: () => void;
  onBenchmark: () => void;
  onRace: () => void;
  onHelp: () => void;
  planned: boolean;
  benchmarkRunning?: boolean;
  raceRunning?: boolean;
}

export default function Header({
  connected,
  speedupBadge,
  onConnect,
  onStart,
  onBenchmark,
  onRace,
  onHelp,
  planned,
  benchmarkRunning,
  raceRunning,
}: HeaderProps) {
  return (
    <header className="app-header">
      <span className="brand">Cerebrain</span>

      <button
        className={`connect-btn ${connected ? 'connected' : ''}`}
        onClick={onConnect}
      >
        {connected ? 'Connected' : 'Connect'}
      </button>

      <nav className="header-nav">
        <button className="nav-link" onClick={onStart} disabled={!planned || !connected}>
          Start Pipeline
        </button>
        <span className="nav-sep" />
        <button className="nav-link" onClick={onBenchmark} disabled={benchmarkRunning || !connected}>
          {benchmarkRunning ? 'Running…' : 'Benchmark'}
        </button>
        <span className="nav-sep" />
        <button className="nav-link race" onClick={onRace} disabled={!connected}>
          ▶ Race
        </button>
        <span className="nav-sep" />
        <div className="nav-grid" aria-hidden title="Dashboard">
          <span /><span /><span /><span />
          <span /><span /><span /><span />
          <span /><span /><span /><span />
        </div>
        <button className="nav-link" onClick={onHelp}>
          Help
        </button>

        {speedupBadge && (
          <span className={`speedup-badge ${speedupBadge.variant === 'gpu' ? 'gpu' : ''}`}>
            {speedupBadge.text}
          </span>
        )}
      </nav>
    </header>
  );
}
