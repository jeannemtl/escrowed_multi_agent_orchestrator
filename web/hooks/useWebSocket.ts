'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  AgentState,
  AgentStatusKind,
  Attestation,
  BudgetData,
  EscrowState,
  IncomingMessage,
  LayerInfo,
  OutgoingMessage,
  PlannedTask,
  RaceState,
  RaceSideState,
  SpeedupBadge,
  TaskState,
} from '@/types/messages';

const DEFAULT_WS_URL =
  typeof process !== 'undefined' && process.env.NEXT_PUBLIC_WS_URL
    ? process.env.NEXT_PUBLIC_WS_URL
    : 'ws://localhost:8765';

const AGENT_IDS = Array.from({ length: 10 }, (_, i) => `agent-${i + 1}`);

function nowTime(): string {
  if (typeof window === 'undefined') return '';
  return new Date().toLocaleTimeString();
}

function emptyRaceSide(): RaceSideState {
  return {
    tasks: {},
    layers: {},
    elapsed: 0,
    done: false,
    totalTime: 0,
    wallet: 0,
    released: 0,
  };
}

/* ── Project snapshot type ──
 * Each prompt (plan or race) creates a Project. The main dashboard always
 * reads from the currently selected project. Race state lives independently
 * of projects. */
export interface Project {
  id: string;
  prompt: string;
  date: string;
  plannerStatus: string;
  plannerStatusColor: string;
  plannedTasks: PlannedTask[];
  agents: AgentState[];
  layers: LayerInfo[];
  taskMap: Record<string, TaskState>;
  attestations: Attestation[];
  summary: {
    totalTasks: number;
    totalLayers: number;
    confirmed: number;
    txs: number;
    totalCostCents: number;
    savings: number;
  };
  escrow: EscrowState | null;
}

function emptyProject(id: string, prompt: string, date: string): Project {
  return {
    id,
    prompt,
    date,
    plannerStatus: '',
    plannerStatusColor: '#ffe5a0',
    plannedTasks: [],
    agents: [],
    layers: [],
    taskMap: {},
    attestations: [],
    summary: {
      totalTasks: 0,
      totalLayers: 0,
      confirmed: 0,
      txs: 0,
      totalCostCents: 0,
      savings: 0,
    },
    escrow: null,
  };
}

let PROJECT_SEQ = 0;
function nextProjectId(): string {
  PROJECT_SEQ += 1;
  return `proj-${Date.now().toString(36)}-${PROJECT_SEQ}`;
}

function dateStrNow(): string {
  return new Date()
    .toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
    .replace('/', ' / ');
}

const SEED_PROJECTS: Project[] = [
  emptyProject('seed-1', 'Research IBM sub-nm chip vs Huawei LogicFolding, then synthesize.', 'Dec / 26'),
  emptyProject('seed-2', 'Summarize the HBM4 bandwidth roadmap across vendors.', 'Dec / 27'),
  emptyProject('seed-3', 'Survey UCIe chiplet adoption and interop risks.', 'Dec / 28'),
];

export interface WebSocketApi {
  connected: boolean;
  // current-project-derived (back-compat flat API)
  plannerStatus: string;
  plannerStatusColor: string;
  plannedTasks: PlannedTask[];
  agents: AgentState[];
  layers: LayerInfo[];
  taskMap: Record<string, TaskState>;
  attestations: Attestation[];
  summary: {
    totalTasks: number;
    totalLayers: number;
    confirmed: number;
    txs: number;
    totalCostCents: number;
    savings: number;
  };
  escrow: EscrowState | null;
  // global (not per-project)
  budget: BudgetData;
  halted: { message: string; layer: number; spent: number; budget: number; sig: string } | null;
  benchmark: {
    cerebrasTotal: number;
    glmTotal: number;
    speedup: number;
    tasks: { name: string; cMs: number; gMs: number; speedup: number }[];
  } | null;
  // race
  race: RaceState | null;
  raceOpen: boolean;
  raceMinimized: boolean;
  racePlanning: boolean;
  speedupBadge: SpeedupBadge | null;
  racePrompt: string;
  raceImage: string | null;
  // projects
  projects: Project[];
  currentProjectId: string;
  promptHistory: { id: string; text: string; date: string }[];
  // actions
  connect: () => void;
  disconnect: () => void;
  send: (msg: OutgoingMessage) => void;
  startPipeline: () => void;
  runBenchmark: () => void;
  startRace: (prompt: string, image: string | null) => void;
  closeRace: () => void;
  reopenRace: () => void;
  planAndSubmit: (prompt: string, image: string | null) => void;
  showHelp: () => void;
  setRacePromptImage: (prompt: string, image: string | null) => void;
  selectProject: (id: string) => void;
}

export function useWebSocket(wsUrl: string = DEFAULT_WS_URL): WebSocketApi {
  const wsRef = useRef<WebSocket | null>(null);
  const raceTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const currentProjectIdRef = useRef<string>('seed-3');
  const raceProjectIdRef = useRef<string | null>(null);

  const [connected, setConnected] = useState(false);
  const [projects, setProjects] = useState<Project[]>(SEED_PROJECTS);
  const [currentProjectId, setCurrentProjectId] = useState<string>('seed-3');
  // global (non-project) state
  const [budget, setBudget] = useState<BudgetData>({
    budget_cents: 500,
    spent_cents: 0,
    stripe_mode: 'mock',
  });
  const [halted, setHalted] = useState<WebSocketApi['halted']>(null);
  const [benchmark, setBenchmark] = useState<WebSocketApi['benchmark']>(null);
  const [race, setRace] = useState<RaceState | null>(null);
  const [raceOpen, setRaceOpen] = useState(false);
  const [raceMinimized, setRaceMinimized] = useState(false);
  const [racePlanning, setRacePlanning] = useState(false);
  const [speedupBadge, setSpeedupBadge] = useState<SpeedupBadge | null>(null);
  const [racePrompt, setRacePrompt] = useState('');
  const [raceImage, setRaceImage] = useState<string | null>(null);

  // keep ref in sync so message handlers (memoized) always see latest id
  useEffect(() => {
    currentProjectIdRef.current = currentProjectId;
  }, [currentProjectId]);

  const send = useCallback((msg: OutgoingMessage) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }, []);

  /* ── Project mutator: update only the currently selected project ── */
  const updateCurrent = useCallback((fn: (p: Project) => Project) => {
    setProjects((prev) =>
      prev.map((p) => (p.id === currentProjectIdRef.current ? fn(p) : p)),
    );
  }, []);

  /* ── Project mutator: update the project that owns the current race ── */
  const updateRaceProject = useCallback((fn: (p: Project) => Project) => {
    const pid = raceProjectIdRef.current;
    if (!pid) return;
    setProjects((prev) => prev.map((p) => (p.id === pid ? fn(p) : p)));
  }, []);

  /* ── Timer for race (runs while a race is active, independent of panel visibility) ── */
  useEffect(() => {
    if (race && !race.finish) {
      if (raceTimerRef.current) clearInterval(raceTimerRef.current);
      raceTimerRef.current = setInterval(() => {
        setRace((prev) => {
          if (!prev) return prev;
          const elapsed = (Date.now() - prev.startTime) / 1000;
          return {
            ...prev,
            cerebras: {
              ...prev.cerebras,
              elapsed: prev.cerebras.done ? prev.cerebras.totalTime : elapsed,
            },
            gpu: {
              ...prev.gpu,
              elapsed: prev.gpu.done ? prev.gpu.totalTime : elapsed,
            },
          };
        });
      }, 100);
      return () => {
        if (raceTimerRef.current) {
          clearInterval(raceTimerRef.current);
          raceTimerRef.current = null;
        }
      };
    }
  }, [race?.finish]);

  const connect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch {
      return;
    }
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      send({ type: 'get_status' });
    };

    ws.onmessage = (event) => {
      let msg: IncomingMessage;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      handleMessage(msg);
    };

    ws.onclose = () => {
      setConnected(false);
    };

    ws.onerror = () => {
      setConnected(false);
    };
  }, [wsUrl, send]);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setConnected(false);
  }, []);

  /* ── Helper updaters (operate on the current project) ── */
  const updateAgentStatus = useCallback(
    (agentId: string, status: AgentStatusKind, resultSummary?: string) => {
      updateCurrent((p) => ({
        ...p,
        agents: p.agents.map((a) => {
          if (a.agentId !== agentId) return a;
          const next: AgentState = { ...a, status };
          if (resultSummary !== undefined && a.taskId) {
            next.result = {
              taskId: a.taskId,
              summary: resultSummary,
              verified: status !== 'failed',
            };
          }
          return next;
        }),
      }));
    },
    [updateCurrent],
  );

  const updateTaskCard = useCallback(
    (taskId: string, status: TaskState['status']) => {
      updateCurrent((p) => ({
        ...p,
        taskMap: {
          ...p.taskMap,
          [taskId]: p.taskMap[taskId]
            ? { ...p.taskMap[taskId], status }
            : { taskId, layer: 0, status },
        },
      }));
    },
    [updateCurrent],
  );

  const updateLayerStatusState = useCallback(
    (layerIdx: number, status: string) => {
      updateCurrent((p) => ({
        ...p,
        layers: p.layers.map((l) =>
          l.layer === layerIdx ? { ...l, name: l.name, tasks: l.tasks } : l,
        ),
      }));
    },
    [updateCurrent],
  );

  /* ── Main message handler ── */
  const handleMessage = useCallback(
    (msg: IncomingMessage) => {
      const d = msg.data || {};
      switch (msg.type) {
        case 'planning': {
          if (d.stage === 'starting') {
            updateCurrent((p) => ({
              ...p,
              plannerStatus: '⟳ Connecting to Nemotron 3 Ultra via NemoClaw...',
              plannerStatusColor: '#d4f5ef',
            }));
          } else if (d.stage === 'thinking') {
            updateCurrent((p) => ({
              ...p,
              plannerStatus: '⟳ ' + (d.message || ''),
              plannerStatusColor: '#ffe5a0',
            }));
          }
          break;
        }
        case 'plan_ready': {
          const tasks: PlannedTask[] = d.tasks || [];
          const totalLayers = d.total_layers || 0;
          updateCurrent((p) => {
            const agentList: AgentState[] = tasks.map((task) => ({
              agentId: task.agent_id,
              status: 'idle',
              taskId: task.id,
              taskName: task.name,
              taskDescription: task.description,
              dependencies: task.dependencies,
              verifyMethod: task.verify_method,
            }));
            let layers = p.layers;
            let taskMap = p.taskMap;
            if (d.layers && d.layers.length > 0) {
              layers = d.layers;
              const tm: Record<string, TaskState> = {};
              d.layers.forEach((layer: LayerInfo) => {
                (layer.task_ids || []).forEach((tid, i) => {
                  tm[tid] = {
                    taskId: tid,
                    layer: layer.layer,
                    agent: layer.agents?.[i],
                    name: layer.task_names?.[i],
                    verifyMethod: layer.verify_methods?.[i],
                    status: 'pending',
                  };
                });
              });
              taskMap = tm;
            }
            return {
              ...p,
              plannedTasks: tasks,
              plannerStatus: `Planned ${tasks.length} tasks with ${totalLayers} layers. Click Start Pipeline.`,
              plannerStatusColor: '#d4f5ef',
              agents: agentList,
              layers,
              taskMap,
              summary: {
                ...p.summary,
                totalTasks: d.total_tasks || tasks.length,
                totalLayers,
                totalCostCents: 0,
                savings: (d.total_tasks || tasks.length) - p.summary.txs,
              },
            };
          });
          break;
        }
        case 'plan_error': {
          updateCurrent((p) => ({
            ...p,
            plannerStatus: 'Planning failed: ' + (d.error || 'unknown'),
            plannerStatusColor: '#ffd0cc',
          }));
          break;
        }
        case 'dag_ready': {
          updateCurrent((p) => ({
            ...p,
            layers: d.layers ? d.layers : p.layers,
            summary: {
              ...p.summary,
              totalTasks: d.total_tasks || p.summary.totalTasks,
              totalLayers: d.total_layers || p.summary.totalLayers,
            },
          }));
          break;
        }
        case 'task_progress': {
          const tid = d.task_id;
          const aid = d.agent_id;
          const stage = d.stage;
          const detail = d.detail || '';
          const time = nowTime();
          updateCurrent((p) => ({
            ...p,
            taskMap: p.taskMap[tid]
              ? {
                  ...p.taskMap,
                  [tid]: {
                    ...p.taskMap[tid],
                    progress: { stage, detail, time },
                  },
                }
              : p.taskMap,
            agents: p.agents.map((a) =>
              a.agentId === aid
                ? {
                    ...a,
                    status:
                      stage === 'received' ||
                      stage === 'calling_model' ||
                      stage === 'model_calling' ||
                      stage === 'model_streaming'
                        ? 'busy'
                        : a.status,
                    progress: { stage, detail, time },
                  }
                : a,
            ),
          }));
          break;
        }
        case 'pipeline_started':
          break;
        case 'task_dispatched': {
          updateTaskCard(d.task_id, 'dispatched');
          updateAgentStatus(d.agent_id, 'busy');
          break;
        }
        case 'layer_attesting': {
          updateLayerStatusState(d.layer, 'attesting');
          break;
        }
        case 'layer_started': {
          updateLayerStatusState(d.layer, 'collecting');
          break;
        }
        case 'stripe_charging': {
          setBudget((prev) => ({
            ...prev,
            stripe_mode: d.mode ? d.mode : d.stripe_mode || prev.stripe_mode,
            budget_cents: d.budget_cents ?? prev.budget_cents,
            spent_cents:
              d.total_spent_cents !== undefined
                ? d.total_spent_cents
                : d.remaining_budget_cents !== undefined
                  ? prev.budget_cents - d.remaining_budget_cents
                  : prev.spent_cents,
          }));
          break;
        }
        case 'pipeline_halted': {
          setHalted({
            message: d.message || 'Budget exceeded',
            layer: d.layer,
            spent: (d.total_spent_cents || 0) / 100,
            budget: (d.budget_cents || 0) / 100,
            sig: (d.solana_signature || '').slice(0, 40),
          });
          setBudget((prev) => ({
            ...prev,
            spent_cents: d.total_spent_cents ?? prev.spent_cents,
            budget_cents: d.budget_cents ?? prev.budget_cents,
          }));
          break;
        }
        case 'layer_attested': {
          updateLayerStatusState(d.layer, 'attested');
          const att: Attestation = {
            layer: d.layer,
            task_ids: d.task_ids || [],
            agent_ids: d.agent_ids || [],
            latency_ms: d.latency_ms || 0,
            cost_cents: d.cost_cents || 0,
            signature: d.signature,
            stripe_charge_id: d.stripe_charge_id,
            remaining_budget_cents: d.remaining_budget_cents,
          };
          updateCurrent((p) => ({
            ...p,
            attestations: [att, ...p.attestations],
            summary: {
              ...p.summary,
              txs: p.summary.txs + 1,
              totalCostCents: p.summary.totalCostCents + (d.cost_cents || 0),
              savings: p.summary.totalTasks - (p.summary.txs + 1),
            },
          }));
          (d.task_ids || []).forEach((tid: string) => updateTaskCard(tid, 'confirmed'));
          setBudget((prev) => ({
            ...prev,
            budget_cents: d.budget_cents ?? prev.budget_cents,
            spent_cents:
              d.total_spent_cents !== undefined
                ? d.total_spent_cents
                : d.remaining_budget_cents !== undefined
                  ? prev.budget_cents - d.remaining_budget_cents
                  : prev.spent_cents,
            stripe_mode: d.stripe_mode || prev.stripe_mode,
          }));
          break;
        }
        case 'escrow_released':
        case 'escrow_release': {
          const esc = d.escrow || {};
          const walletsRaw = d.wallets || [];
          const walletList = Array.isArray(walletsRaw)
            ? walletsRaw
            : Object.values(walletsRaw);
          updateCurrent((p) => ({
            ...p,
            escrow: {
              available_cents: esc.available_cents || 0,
              released_cents: esc.released_cents || 0,
              wallets: walletList,
              releases: d.releases || [],
              layer: d.layer,
            },
          }));
          break;
        }
        case 'pipeline_complete': {
          updateCurrent((p) => ({
            ...p,
            summary: {
              ...p.summary,
              totalTasks: d.total_tasks ?? p.summary.totalTasks,
              totalLayers: d.total_layers ?? p.summary.totalLayers,
              totalCostCents: d.total_cost_cents ?? p.summary.totalCostCents,
            },
          }));
          break;
        }
        case 'benchmark_results': {
          const cerebras = d.cerebras || {};
          const glm = d.glm || {};
          const speedup = d.total_speedup || 0;
          const cTasks = cerebras.tasks || [];
          const gTasks = glm.tasks || [];
          setBenchmark({
            cerebrasTotal: cerebras.total_time_s || 0,
            glmTotal: glm.total_time_s || 0,
            speedup,
            tasks: cTasks.map((ct: any, i: number) => {
              const gt = gTasks[i] || {};
              const cMs = ct.latency_ms || 0;
              const gMs = gt.latency_ms || 0;
              const tSpeedup = gMs > 0 && cMs > 0 ? gMs / cMs : 0;
              return {
                name: ct.task_name || ct.task_id || '?',
                cMs,
                gMs,
                speedup: tSpeedup,
              };
            }),
          });
          break;
        }
        /* ── Race messages: update race state independently ── */
        case 'comparison_start': {
          setRacePlanning(false);
          const layersData = d.layers || [];
          const budgetCents = d.budget_cents || 500;
          let promptDisplay = 'Default: Cerebras topic decomposition';
          if (raceImage) {
            promptDisplay =
              '📷 Image uploaded — ' +
              (racePrompt ? racePrompt.slice(0, 150) : 'vision model analyzing image content...');
          } else if (racePrompt) {
            promptDisplay = 'Prompt: ' + racePrompt.slice(0, 200);
          }

          const stepsBase: Record<string, 'pending' | 'active' | 'done'> = {
            'attest-0': 'pending',
            'attest-1': 'pending',
            confirm: 'pending',
            escrow: 'pending',
          };
          setRace({
            layers: layersData,
            cerebras: emptyRaceSide(),
            gpu: emptyRaceSide(),
            startTime: Date.now(),
            budget: budgetCents,
            cerebrasModel: d.cerebras_model || 'gemma-4-31b',
            glmModel: d.glm_model || 'z-ai/glm-5.2',
            promptDisplay,
            decomposition: layersData,
            steps: {
              cerebras: { ...stepsBase },
              gpu: { ...stepsBase },
            },
          });
          setRaceOpen(true);
          setRaceMinimized(false);

          // ── Populate the race project's agents, layers, and taskMap
          // from the Cerebras decomposition so the main dashboard shows
          // the task structure immediately. ──
          updateRaceProject((p) => {
            const agentList: AgentState[] = [];
            const tm: Record<string, TaskState> = {};
            const projectLayers: LayerInfo[] = [];
            let agentIdx = 0;
            layersData.forEach((layer: any) => {
              const taskIds: string[] = [];
              const taskNames: string[] = [];
              const agents: string[] = [];
              const verifyMethods: string[] = [];
              (layer.tasks || []).forEach((task: any) => {
                const aid = `agent-${(agentIdx % 5) + 1}`;
                agentIdx++;
                taskIds.push(task.id);
                taskNames.push(task.name);
                agents.push(aid);
                verifyMethods.push('content_len');
                tm[task.id] = {
                  taskId: task.id,
                  layer: layer.layer,
                  agent: aid,
                  name: task.name,
                  verifyMethod: 'content_len',
                  status: 'pending',
                };
                agentList.push({
                  agentId: aid,
                  status: 'idle',
                  taskId: task.id,
                  taskName: task.name,
                  taskDescription: task.prompt || task.name,
                  dependencies: [],
                  verifyMethod: 'content_len',
                });
              });
              projectLayers.push({
                layer: layer.layer,
                total_downstream: 0,
                task_ids: taskIds,
                task_names: taskNames,
                agents,
                verify_methods: verifyMethods,
              });
            });
            return {
              ...p,
              agents: agentList,
              layers: projectLayers,
              taskMap: tm,
              plannedTasks: layersData.flatMap((l: any) =>
                (l.tasks || []).map((t: any) => ({
                  id: t.id,
                  name: t.name,
                  description: t.prompt || t.name,
                  agent_id: `agent-1`,
                  dependencies: [],
                  verify_method: 'content_len',
                })),
              ),
              summary: {
                ...p.summary,
                totalTasks: agentList.length,
                totalLayers: layersData.length,
              },
            };
          });
          break;
        }
        case 'comparison_planning': {
          // Show the planning message inside the race panel; the current
          // project (the one the race was launched from) carries the status.
          updateCurrent((p) => ({
            ...p,
            plannerStatus:
              d.message ||
              'Analyzing image with vision model, then decomposing into race tasks...',
            plannerStatusColor: '#ffe5a0',
          }));
          setRacePlanning(true);
          // Open the panel so the user sees the planning state.
          setRaceOpen(true);
          setRaceMinimized(false);
          break;
        }
        case 'comparison_progress': {
          setRace((prev) => {
            if (!prev) return prev;
            const side = d.side as 'cerebras' | 'gpu';
            const sideState = prev[side];
            if (!sideState) return prev;
            const taskId = d.task_id;
            const stage = d.stage;
            const newTasks = { ...sideState.tasks };
            if (stage === 'done') {
              newTasks[taskId] = {
                done: true,
                ok: d.ok,
                latency_s: d.latency_s,
              };
            } else if (stage === 'error') {
              newTasks[taskId] = { done: true, error: d.error };
            } else if (stage === 'starting') {
              newTasks[taskId] = { done: false };
            }
            return { ...prev, [side]: { ...sideState, tasks: newTasks } };
          });
          // ── Update the main dashboard for Cerebras-side progress ──
          if (d.side === 'cerebras') {
            const taskId = d.task_id;
            const stage = d.stage;
            if (stage === 'starting') {
              updateRaceProject((p) => ({
                ...p,
                taskMap: {
                  ...p.taskMap,
                  [taskId]: p.taskMap[taskId]
                    ? { ...p.taskMap[taskId], status: 'dispatched' }
                    : { taskId, layer: 0, status: 'dispatched' },
                },
                agents: p.agents.map((a) =>
                  a.taskId === taskId ? { ...a, status: 'busy' as const } : a,
                ),
              }));
            } else if (stage === 'done') {
              updateRaceProject((p) => ({
                ...p,
                taskMap: {
                  ...p.taskMap,
                  [taskId]: p.taskMap[taskId]
                    ? {
                        ...p.taskMap[taskId],
                        status: 'verified',
                        outputPreview: d.content_preview || '',
                        outputLength: d.content_length || 0,
                        latency_s: d.latency_s,
                      }
                    : { taskId, layer: 0, status: 'verified' },
                },
                agents: p.agents.map((a) =>
                  a.taskId === taskId
                    ? {
                        ...a,
                        status: 'done' as const,
                        result: {
                          taskId,
                          summary: d.content_preview || '(completed)',
                          verified: d.ok !== false,
                        },
                      }
                    : a,
                ),
              }));
            } else if (stage === 'error') {
              updateRaceProject((p) => ({
                ...p,
                taskMap: {
                  ...p.taskMap,
                  [taskId]: p.taskMap[taskId]
                    ? { ...p.taskMap[taskId], status: 'failed' }
                    : { taskId, layer: 0, status: 'failed' },
                },
                agents: p.agents.map((a) =>
                  a.taskId === taskId ? { ...a, status: 'failed' as const } : a,
                ),
              }));
            }
          }
          break;
        }
        case 'comparison_layer': {
          setRace((prev) => {
            if (!prev) return prev;
            const side = d.side as 'cerebras' | 'gpu';
            const layerIdx = d.layer;
            const sideState = prev[side];
            return {
              ...prev,
              [side]: {
                ...sideState,
                layers: {
                  ...sideState.layers,
                  [layerIdx]: {
                    status: d.stage || 'dispatching',
                    taskCount: d.task_count,
                  },
                },
              },
            };
          });
          break;
        }
        case 'comparison_attested': {
          setRace((prev) => {
            if (!prev) return prev;
            const side = d.side as 'cerebras' | 'gpu';
            const layerIdx = d.layer;
            const sideState = prev[side];
            const newLayers = {
              ...sideState.layers,
              [layerIdx]: {
                status: 'attested',
                attested: true,
                verifiedCount: d.verified_count,
                taskCount: d.task_count,
                latencyMs: d.latency_ms,
                costCents: d.cost_cents,
                signature: d.signature,
              },
            };
            const steps = { ...prev.steps };
            const sideSteps = { ...steps[side] };
            const key = `attest-${layerIdx}`;
            sideSteps[key] = 'done';
            const nextKey = `attest-${layerIdx + 1}`;
            if (sideSteps[nextKey] !== undefined) {
              sideSteps[nextKey] = 'active';
            } else {
              sideSteps.confirm = 'done';
              sideSteps.escrow = 'active';
            }
            steps[side] = sideSteps;
            return {
              ...prev,
              [side]: { ...sideState, layers: newLayers },
              steps,
            };
          });
          // ── Add attestation to the project for Cerebras side ──
          if (d.side === 'cerebras') {
            updateRaceProject((p: Project) => {
              // Collect task IDs for this layer from the project's layers
              const layerInfo = p.layers.find((l) => l.layer === d.layer);
              const taskIds = layerInfo?.task_ids || [];
              const att: Attestation = {
                layer: d.layer,
                task_ids: taskIds,
                agent_ids: [],
                latency_ms: d.latency_ms || 0,
                cost_cents: d.cost_cents || 0,
                signature: d.signature || '',
                stripe_charge_id: undefined,
                remaining_budget_cents: undefined,
              };
              return {
                ...p,
                attestations: [att, ...p.attestations],
                summary: {
                  ...p.summary,
                  txs: p.summary.txs + 1,
                  totalCostCents: p.summary.totalCostCents + (d.cost_cents || 0),
                  confirmed: p.summary.confirmed + 1,
                },
              };
            });
          }
          break;
        }
        case 'comparison_escrow': {
          setRace((prev) => {
            if (!prev) return prev;
            const side = d.side as 'cerebras' | 'gpu';
            const sideState = prev[side];
            const totalBudget = d.total_budget_cents || prev.budget || 500;
            const remaining = d.remaining_budget_cents;
            const steps = { ...prev.steps };
            const sideSteps = { ...steps[side] };
            if (remaining <= 0 || (totalBudget > 0 && ((totalBudget - remaining) / totalBudget) * 100 >= 99)) {
              sideSteps.escrow = 'done';
            }
            steps[side] = sideSteps;
            return {
              ...prev,
              [side]: {
                ...sideState,
                released: d.released_cents,
                wallet: d.agent_wallet_cents,
              },
              steps,
            };
          });
          break;
        }
        case 'comparison_side_done': {
          setRace((prev) => {
            if (!prev) return prev;
            const side = d.side as 'cerebras' | 'gpu';
            const totalTime = d.total_time_s || 0;
            const successful = d.successful || 0;
            const totalTasks = d.total_tasks || 0;
            const sideState = prev[side];
            const updatedSide: RaceSideState = {
              ...sideState,
              done: true,
              totalTime,
              finishStamp: { time: totalTime, successful, totalTasks },
            };
            const next = { ...prev, [side]: updatedSide };
            if (next.cerebras.done && next.gpu.done) {
              const cTime = next.cerebras.totalTime;
              const gTime = next.gpu.totalTime;
              let speedup = 0;
              let winner: 'cerebras' | 'gpu' = 'cerebras';
              if (cTime > 0 && gTime > 0) {
                speedup = gTime / cTime;
                winner = speedup >= 1 ? 'cerebras' : 'gpu';
              }
              next.finish = {
                speedup,
                winner,
                cerebrasTime: cTime,
                gpuTime: gTime,
                cerebrasSuccessful: next.cerebras.finishStamp?.successful || 0,
                cerebrasTotal: next.cerebras.finishStamp?.totalTasks || 0,
                gpuSuccessful: next.gpu.finishStamp?.successful || 0,
                gpuTotal: next.gpu.finishStamp?.totalTasks || 0,
              };
              if (speedup > 0) {
                if (speedup > 1) {
                  setSpeedupBadge({
                    text: `⚡ Cerebras ${speedup.toFixed(1)}x faster`,
                    variant: 'cerebras',
                  });
                } else {
                  setSpeedupBadge({
                    text: `⚡ GPU ${(1 / speedup).toFixed(1)}x faster`,
                    variant: 'gpu',
                  });
                }
              }
            }
            return next;
          });
          break;
        }
        case 'comparison_complete': {
          setRace((prev) => {
            if (!prev) return prev;
            const cTotal = d.cerebras?.total_time_s || 0;
            const gTotal = d.gpu?.total_time_s || 0;
            const speedup = d.total_speedup || 0;
            const winner = d.winner || 'cerebras';
            const c = d.cerebras || { total_time_s: 0, successful: 0, tasks: [] };
            const g = d.gpu || { total_time_s: 0, successful: 0, tasks: [] };
            const next: RaceState = {
              ...prev,
              cerebras: {
                ...prev.cerebras,
                done: true,
                totalTime: cTotal,
                finishStamp: {
                  time: cTotal,
                  successful: c.successful || 0,
                  totalTasks: c.tasks?.length || 0,
                },
              },
              gpu: {
                ...prev.gpu,
                done: true,
                totalTime: gTotal,
                finishStamp: {
                  time: gTotal,
                  successful: g.successful || 0,
                  totalTasks: g.tasks?.length || 0,
                },
              },
              finish: {
                speedup,
                winner,
                cerebrasTime: cTotal,
                gpuTime: gTotal,
                cerebrasSuccessful: c.successful || 0,
                cerebrasTotal: c.tasks?.length || 0,
                gpuSuccessful: g.successful || 0,
                gpuTotal: g.tasks?.length || 0,
              },
            };
            return next;
          });
          if (d.total_speedup && d.total_speedup > 0) {
            const speedup = d.total_speedup;
            if (speedup > 1) {
              setSpeedupBadge({
                text: `⚡ Cerebras ${speedup.toFixed(1)}x faster`,
                variant: 'cerebras',
              });
            } else {
              setSpeedupBadge({
                text: `⚡ GPU ${(1 / speedup).toFixed(1)}x faster`,
                variant: 'gpu',
              });
            }
          }
          break;
        }
        case 'comparison_error': {
          setRacePlanning(false);
          if (typeof window !== 'undefined') {
            alert('Race error: ' + (d.error || 'unknown'));
          }
          break;
        }
        case 'status': {
          updateCurrent((p) => {
            let layers = p.layers;
            let taskMap = p.taskMap;
            if (d.layers) {
              layers = d.layers;
              const tm: Record<string, TaskState> = {};
              d.layers.forEach((layer: LayerInfo) => {
                (layer.task_ids || []).forEach((tid: string, i: number) => {
                  tm[tid] = {
                    taskId: tid,
                    layer: layer.layer,
                    agent: layer.agents?.[i],
                    name: layer.task_names?.[i],
                    verifyMethod: layer.verify_methods?.[i],
                    status: 'pending',
                  };
                });
              });
              taskMap = tm;
            }
            let agents = p.agents;
            if (d.agent_registry?.agents) {
              if (p.agents.length !== 0) {
                agents = p.agents.map((a) => {
                  const info = d.agent_registry.agents[a.agentId];
                  if (!info) return a;
                  let status: AgentStatusKind = a.status;
                  if (info.status === 'idle' && (info.completed_count || 0) > 0)
                    status = 'done';
                  else if (info.status === 'busy') status = 'busy';
                  return { ...a, status };
                });
              }
            }
            return {
              ...p,
              layers,
              taskMap,
              agents,
              summary: {
                ...p.summary,
                totalTasks: d.total_tasks ?? p.summary.totalTasks,
                totalLayers: d.total_layers ?? p.summary.totalLayers,
                totalCostCents: d.total_cost_cents ?? p.summary.totalCostCents,
                confirmed: d.by_status?.confirmed?.length ?? p.summary.confirmed,
              },
            };
          });
          break;
        }
        case 'task_verified': {
          if (d.ok) {
            updateTaskCard(d.task_id, 'verified');
            updateAgentStatus(d.agent_id, 'done', d.summary || '(no output)');
          } else {
            updateTaskCard(d.task_id, 'failed');
            updateAgentStatus(d.agent_id, 'failed', '[FAILED] ' + (d.error || 'verification failed'));
          }
          break;
        }
        case 'task_result': {
          if (d.task_id && d.agent_id && d.summary) {
            updateAgentStatus(d.agent_id, 'done', d.summary);
            updateTaskCard(d.task_id, 'verified');
          }
          break;
        }
        case 'pong':
          break;
        default:
          break;
      }
    },
    [raceImage, racePrompt, updateAgentStatus, updateTaskCard, updateLayerStatusState, updateCurrent, updateRaceProject],
  );

  /* ── Public actions ── */
  const startPipeline = useCallback(() => {
    send({ type: 'start' });
  }, [send]);

  const runBenchmark = useCallback(() => {
    send({ type: 'run_benchmark' });
  }, [send]);

  const startRace = useCallback(
    (prompt: string, image: string | null) => {
      setRacePrompt(prompt);
      setRaceImage(image);
      // Create a new project for this race prompt and select it.
      const text = prompt || (image ? '📷 Image analysis' : 'Untitled race');
      const proj = emptyProject(nextProjectId(), text.slice(0, 80), dateStrNow());
      setProjects((prev) => [...prev, proj]);
      currentProjectIdRef.current = proj.id;
      setCurrentProjectId(proj.id);
      raceProjectIdRef.current = proj.id;
      setRacePlanning(true);
      setRaceOpen(true);
      setRaceMinimized(false);
      const msg: OutgoingMessage = { type: 'run_comparison', prompt } as any;
      if (image) (msg as any).image = image;
      send(msg);
    },
    [send],
  );

  // closeRace = minimize (hide panel) but keep the race running.
  const closeRace = useCallback(() => {
    setRaceOpen(false);
    setRaceMinimized(true);
  }, []);

  const reopenRace = useCallback(() => {
    setRaceOpen(true);
    setRaceMinimized(false);
  }, []);

  const planAndSubmit = useCallback(
    (prompt: string, image: string | null) => {
      if (!prompt && !image) return;
      const text = prompt || (image ? '📷 Image analysis' : 'Untitled task');
      const proj = emptyProject(nextProjectId(), text.slice(0, 80), dateStrNow());
      setProjects((prev) => [...prev, proj]);
      currentProjectIdRef.current = proj.id;
      setCurrentProjectId(proj.id);
      const status = image
        ? 'Planning: analyzing image with vision model, then decomposing...'
        : 'Planning: calling Nemotron 3 Ultra to decompose prompt...';
      updateCurrent((p) => ({
        ...p,
        plannerStatus: status,
        plannerStatusColor: '#ffe5a0',
      }));
      const msg: OutgoingMessage = { type: 'plan_tasks', prompt } as any;
      if (image) (msg as any).image = image;
      send(msg);
    },
    [send, updateCurrent],
  );

  const showHelp = useCallback(() => {
    if (typeof window !== 'undefined') {
      alert(
        'Cerebrain Multi-Agent Orchestrator\n\n' +
          '1. Connect to the WebSocket bridge (default ws://localhost:8765).\n' +
          '2. Type a prompt or upload an image.\n' +
          '3. Click "Plan & Submit" to decompose into tasks.\n' +
          '4. Click "Start Pipeline" to dispatch agents.\n' +
          '5. Click "Race" to run Cerebras vs GPU comparison.\n' +
          '6. Click "Benchmark" for a speed comparison report.',
      );
    }
  }, []);

  const setRacePromptImage = useCallback((prompt: string, image: string | null) => {
    setRacePrompt(prompt);
    setRaceImage(image);
  }, []);

  const selectProject = useCallback((id: string) => {
    currentProjectIdRef.current = id;
    setCurrentProjectId(id);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (raceTimerRef.current) clearInterval(raceTimerRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  /* ── Derived: current project (back-compat flat API) ── */
  const currentProject =
    projects.find((p) => p.id === currentProjectId) || projects[projects.length - 1] || SEED_PROJECTS[0];

  const promptHistory = projects.map((p) => ({ id: p.id, text: p.prompt, date: p.date }));

  return {
    connected,
    plannerStatus: currentProject.plannerStatus,
    plannerStatusColor: currentProject.plannerStatusColor,
    plannedTasks: currentProject.plannedTasks,
    agents: currentProject.agents,
    layers: currentProject.layers,
    taskMap: currentProject.taskMap,
    attestations: currentProject.attestations,
    summary: currentProject.summary,
    escrow: currentProject.escrow,
    budget,
    halted,
    benchmark,
    race,
    raceOpen,
    raceMinimized,
    racePlanning,
    speedupBadge,
    racePrompt,
    raceImage,
    projects,
    currentProjectId,
    promptHistory,
    connect,
    disconnect,
    send,
    startPipeline,
    runBenchmark,
    startRace,
    closeRace,
    reopenRace,
    planAndSubmit,
    showHelp,
    setRacePromptImage,
    selectProject,
  };
}
