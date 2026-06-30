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
    // If a race is live (not finished), just reopen the panel.
    if (ws.race && !ws.race.finish) {
      ws.reopenRace();
      return;
    }
    // Race is finished or none yet — start a new one.
    ws.startRace(prompt, image);
    setRaceRunning(true);
  }

  function handleCloseRace() {
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

  const raceDone =
    ws.race?.finish || (ws.race?.cerebras.done && ws.race?.gpu.done);

  useEffect(() => {
    if (raceDone && raceRunning) {
      setRaceRunning(false);
    }
  }, [raceDone, raceRunning]);

  const raceLive = !!ws.race && !raceDone;
  const showReopenButton = raceLive && ws.raceMinimized;

  const ctaSubtitle = ws.speedupBadge
    ? `${ws.speedupBadge.text} — settled on-chain.`
    : 'Cerebras × GPU — race them, settle on-chain.';

  return (
    <div className="page">
      <div className="container">
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

        <div className="three-col">
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
        </div>

        {ws.benchmark && <BenchmarkPanel benchmark={ws.benchmark} />}

        <div className="big-cta" onClick={handleRace}>
          <div className="big-cta-text">Watch them race.</div>
          <div className="big-cta-sub">{ctaSubtitle}</div>
        </div>

        <Footer onHelp={ws.showHelp} />
      </div>

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
    </div>
  );
}
