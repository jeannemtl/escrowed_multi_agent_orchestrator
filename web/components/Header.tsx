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
      <div className="brand">
        <div className="logo-mark">C</div>
        Cerebrain
      </div>
      <div className="header-right">
        <div className="conn-status">
          <span className={`conn-dot ${connected ? '' : 'offline'}`} />
          <span>{connected ? 'Connected' : 'Disconnected'}</span>
        </div>
        <button className="nav-item" onClick={onConnect}>
          {connected ? 'Connected' : 'Connect'}
        </button>
        <nav className="nav-items">
          <button className="nav-item" onClick={onStart} disabled={!planned || !connected}>
            Start Pipeline
          </button>
          <span className="nav-sep">|</span>
          <button className="nav-item" onClick={onBenchmark} disabled={benchmarkRunning || !connected}>
            {benchmarkRunning ? 'Running...' : 'Benchmark'}
          </button>
          <span className="nav-sep">|</span>
          <button className="nav-item race" onClick={onRace} disabled={raceRunning || !connected}>
            {raceRunning ? 'Racing...' : '▶ Race'}
          </button>
          <span className="nav-sep">|</span>
          <button className="nav-item" onClick={onHelp}>
            Help
          </button>
        </nav>
        {speedupBadge && (
          <span className={`speedup-badge ${speedupBadge.variant === 'gpu' ? 'gpu' : ''}`}>
            {speedupBadge.text}
          </span>
        )}
      </div>
    </header>
  );
}
