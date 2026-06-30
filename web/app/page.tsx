'use client';

import { useEffect, useState } from 'react';
import Header from '@/components/Header';
import SearchBar from '@/components/SearchBar';
import PromptHistory from '@/components/PromptHistory';
import SummaryBar from '@/components/SummaryBar';
import BudgetBar from '@/components/BudgetBar';
import AgentPrompts from '@/components/AgentPrompts';
import TaskDAG from '@/components/TaskDAG';
import OnChainLedger from '@/components/OnChainLedger';
import BenchmarkPanel from '@/components/BenchmarkPanel';
import RacePanel from '@/components/RacePanel';
import Footer from '@/components/Footer';
import { useWebSocket } from '@/hooks/useWebSocket';

export default function Page() {
  const ws = useWebSocket();
  const [prompt, setPrompt] = useState('');
  const [image, setImage] = useState<string | null>(null);
  const [benchmarkRunning, setBenchmarkRunning] = useState(false);
  const [raceRunning, setRaceRunning] = useState(false);

  // Read dashboard data from the currently selected project (back-compat
  // flat API on ws already derives from the current project).
  const planned = ws.plannedTasks.length > 0;

  function handleConnect() {
    ws.connect();
  }

  function handleStart() {
    ws.startPipeline();
  }

  function handleBenchmark() {
    ws.runBenchmark();
    setBenchmarkRunning(true);
    setTimeout(() => setBenchmarkRunning(false), 180000);
  }

  function handleRace() {
    ws.startRace(prompt, image);
    setRaceRunning(true);
  }

  function handleCloseRace() {
    // Minimize the panel; the race keeps running in the background.
    ws.closeRace();
  }

  function handleReopenRace() {
    ws.reopenRace();
  }

  function handlePromptImageChange(p: string, img: string | null) {
    setPrompt(p);
    setImage(img);
    ws.setRacePromptImage(p, img);
  }

  function handlePlan(p: string, img: string | null) {
    ws.planAndSubmit(p, img);
  }

  function handleSelectProject(id: string) {
    ws.selectProject(id);
  }

  // The race "running" state should clear once a finish or side_done arrives.
  const raceDone =
    ws.race?.finish || (ws.race?.cerebras.done && ws.race?.gpu.done);

  useEffect(() => {
    if (raceDone && raceRunning) {
      setRaceRunning(false);
    }
  }, [raceDone, raceRunning]);

  // Show the floating reopen button while a race is live but the panel is hidden.
  const raceLive = !!ws.race && !raceDone;
  const showReopenButton = raceLive && ws.raceMinimized;

  return (
    <>
      <Header
        connected={ws.connected}
        speedupBadge={ws.speedupBadge}
        onConnect={handleConnect}
        onStart={handleStart}
        onBenchmark={handleBenchmark}
        onRace={handleRace}
        onHelp={ws.showHelp}
        planned={planned}
        benchmarkRunning={benchmarkRunning}
        raceRunning={raceRunning}
      />

      <SearchBar
        connected={ws.connected}
        plannerStatus={ws.plannerStatus}
        plannerStatusColor={ws.plannerStatusColor}
        onPlan={handlePlan}
        onPromptImageChange={handlePromptImageChange}
      />

      <PromptHistory
        items={ws.promptHistory}
        activeId={ws.currentProjectId}
        onSelect={handleSelectProject}
      />

      <SummaryBar
        totalTasks={ws.summary.totalTasks}
        totalLayers={ws.summary.totalLayers}
        confirmed={ws.summary.confirmed}
        txs={ws.summary.txs}
        totalCostCents={ws.summary.totalCostCents}
        savings={ws.summary.savings}
      />

      <BudgetBar budget={ws.budget} halted={!!ws.halted} />

      <div className="main-grid">
        <AgentPrompts agents={ws.agents} />
        <TaskDAG
          layers={ws.layers}
          taskMap={ws.taskMap}
          plannedTasks={ws.plannedTasks}
        />
        <OnChainLedger
          attestations={ws.attestations}
          escrow={ws.escrow}
          halted={ws.halted}
          totalCostCents={ws.summary.totalCostCents}
          savings={ws.summary.savings}
        />
        <BenchmarkPanel benchmark={ws.benchmark} />
      </div>

      <div className="race-cta" onClick={handleRace}>
        <span className="cta-text">
          Watch them race. <span className="arrow">▶</span>
        </span>
      </div>

      <Footer onHelp={ws.showHelp} />

      <RacePanel
        race={ws.race}
        open={ws.raceOpen}
        onClose={handleCloseRace}
        planning={ws.racePlanning}
        plannerStatus={ws.plannerStatus}
        plannerStatusColor={ws.plannerStatusColor}
      />

      {showReopenButton && (
        <button
          className="race-reopen-fab"
          onClick={handleReopenRace}
          title="Show race panel"
        >
          🏁 Race Live
        </button>
      )}
    </>
  );
}
