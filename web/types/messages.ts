// TypeScript types for all Cerebrain WebSocket messages.

/* ── Outgoing messages ── */

export interface PlanTasksMsg {
  type: 'plan_tasks';
  prompt: string;
  image?: string;
}

export interface StartMsg {
  type: 'start';
}

export interface RunBenchmarkMsg {
  type: 'run_benchmark';
}

export interface RunComparisonMsg {
  type: 'run_comparison';
  prompt: string;
  image?: string;
}

export interface RegisterAgentMsg {
  type: 'register_agent';
  agent_id: string;
}

export interface SubmitTaskMsg {
  type: 'submit_task';
  task_id: string;
  agent_id: string;
}

export interface PingMsg {
  type: 'ping';
}

export interface GetStatusMsg {
  type: 'get_status';
}

export type OutgoingMessage =
  | PlanTasksMsg
  | StartMsg
  | RunBenchmarkMsg
  | RunComparisonMsg
  | RegisterAgentMsg
  | SubmitTaskMsg
  | PingMsg
  | GetStatusMsg;

/* ── Planned task / layer data ── */

export interface PlannedTask {
  id: string;
  name: string;
  description: string;
  agent_id: string;
  dependencies?: string[];
  verify_method?: string;
}

export interface LayerInfo {
  layer: number;
  name?: string;
  tasks?: PlannedTask[];
  task_ids?: string[];
  task_names?: string[];
  agents?: string[];
  verify_methods?: string[];
  total_downstream?: number;
}

/* ── Incoming message data shapes ── */

export interface PlanningData {
  stage: 'starting' | 'thinking';
  message?: string;
}

export interface PlanReadyData {
  tasks: PlannedTask[];
  total_layers: number;
  total_tasks?: number;
  layers?: LayerInfo[];
}

export interface PlanErrorData {
  error: string;
}

export interface TaskProgressData {
  task_id: string;
  agent_id: string;
  stage: string;
  detail: string;
}

export interface TaskDispatchedData {
  task_id: string;
  agent_id: string;
}

export interface LayerAttestingData {
  layer: number;
}

export interface StripeChargingData {
  mode?: string;
  agent_count?: number;
  budget_cents?: number;
  total_spent_cents?: number;
  remaining_budget_cents?: number;
  stripe_mode?: string;
}

export interface PipelineHaltedData {
  message?: string;
  layer: number;
  total_spent_cents?: number;
  budget_cents?: number;
  solana_signature?: string;
}

export interface LayerAttestedData {
  layer: number;
  task_ids?: string[];
  agent_ids?: string[];
  latency_ms?: number;
  cost_cents?: number;
  signature?: string;
  stripe_charge_id?: string;
  remaining_budget_cents?: number;
  budget_cents?: number;
  total_spent_cents?: number;
  stripe_mode?: string;
}

export interface EscrowWallet {
  agent_id?: string;
  balance_cents?: number;
  total_tasks?: number;
  task_count?: number;
  success_rate?: string | number;
  status?: string;
  exists?: boolean;
}

export interface EscrowRelease {
  agent_id?: string;
  amount_cents?: number;
  transfer_id?: string;
  ok?: boolean;
}

export interface EscrowReleasedData {
  escrow?: {
    released_cents?: number;
    available_cents?: number;
  };
  wallets?: EscrowWallet[] | Record<string, EscrowWallet>;
  releases?: EscrowRelease[];
  layer?: number;
}

export interface BenchmarkTask {
  task_id?: string;
  task_name?: string;
  latency_ms?: number;
}

export interface BenchmarkSide {
  tasks?: BenchmarkTask[];
  total_time_s?: number;
}

export interface BenchmarkResultsData {
  cerebras?: BenchmarkSide;
  glm?: BenchmarkSide;
  total_speedup?: number;
}

export interface ComparisonStartData {
  layers: LayerInfo[];
  cerebras_model?: string;
  glm_model?: string;
  budget_cents?: number;
}

export interface ComparisonPlanningData {
  message?: string;
}

export interface ComparisonProgressData {
  side: 'cerebras' | 'gpu';
  task_id: string;
  stage: 'starting' | 'done' | 'error' | string;
  ok?: boolean;
  latency_s?: number;
  content_preview?: string;
  content_length?: number;
  error?: string;
}

export interface ComparisonLayerData {
  side: 'cerebras' | 'gpu';
  layer: number;
  stage?: string;
  task_count: number;
}

export interface ComparisonAttestedData {
  side: 'cerebras' | 'gpu';
  layer: number;
  verified_count: number;
  task_count: number;
  latency_ms: number;
  cost_cents: number;
  signature?: string;
}

export interface ComparisonEscrowData {
  side: 'cerebras' | 'gpu';
  released_cents: number;
  agent_wallet_cents: number;
  total_budget_cents?: number;
  remaining_budget_cents: number;
}

export interface ComparisonSideDoneData {
  side: 'cerebras' | 'gpu';
  total_time_s?: number;
  successful?: number;
  total_tasks?: number;
}

export interface ComparisonCompleteSide {
  total_time_s: number;
  successful: number;
  tasks: BenchmarkTask[];
}

export interface ComparisonCompleteData {
  cerebras: ComparisonCompleteSide;
  gpu: ComparisonCompleteSide;
  total_speedup?: number;
  winner?: 'cerebras' | 'gpu';
}

export interface ComparisonErrorData {
  error?: string;
}

export interface StatusByStatus {
  confirmed?: string[];
  pending?: string[];
  dispatched?: string[];
  submitted?: string[];
  verified?: string[];
  failed?: string[];
  [key: string]: string[] | undefined;
}

export interface AgentRegistryInfo {
  status?: string;
  completed_count?: number;
}

export interface StatusData {
  layers?: LayerInfo[];
  total_tasks?: number;
  total_layers?: number;
  total_cost_cents?: number;
  by_status?: StatusByStatus;
  agent_registry?: { agents?: Record<string, AgentRegistryInfo> };
}

export interface TaskVerifiedData {
  ok: boolean;
  task_id: string;
  agent_id: string;
  summary?: string;
  error?: string;
}

/* ── Incoming message envelope ── */

export interface IncomingMessage {
  type: string;
  data?: any;
}

/* ── UI state types ── */

export type AgentStatusKind = 'idle' | 'busy' | 'done' | 'failed';

export interface AgentState {
  agentId: string;
  status: AgentStatusKind;
  taskId?: string;
  taskName?: string;
  taskDescription?: string;
  dependencies?: string[];
  verifyMethod?: string;
  progress?: { stage: string; detail: string; time: string };
  result?: { taskId: string; summary: string; verified: boolean };
}

export interface TaskState {
  taskId: string;
  layer: number;
  agent?: string;
  name?: string;
  verifyMethod?: string;
  status: 'pending' | 'dispatched' | 'submitted' | 'verified' | 'confirmed' | 'failed';
  progress?: { stage: string; detail: string; time: string };
  resultSummary?: string;
  outputPreview?: string;
  outputLength?: number;
  latency_s?: number;
}

export interface Attestation {
  layer: number;
  task_ids: string[];
  agent_ids: string[];
  latency_ms: number;
  cost_cents: number;
  signature?: string;
  stripe_charge_id?: string;
  remaining_budget_cents?: number;
}

export interface BudgetData {
  budget_cents: number;
  spent_cents: number;
  stripe_mode: string;
}

export interface EscrowState {
  available_cents: number;
  released_cents: number;
  wallets: EscrowWallet[];
  releases: EscrowRelease[];
  layer?: number;
}

export interface RaceTaskState {
  done?: boolean;
  ok?: boolean;
  error?: string;
  latency_s?: number;
}

export interface RaceSideState {
  tasks: Record<string, RaceTaskState>;
  layers: Record<number, { status: string; verifiedCount?: number; taskCount?: number; attested?: boolean; latencyMs?: number; costCents?: number; signature?: string }>;
  elapsed: number;
  done: boolean;
  totalTime: number;
  wallet: number;
  released: number;
  finishStamp?: { time: number; successful: number; totalTasks: number };
}

export interface RaceState {
  layers: LayerInfo[];
  cerebras: RaceSideState;
  gpu: RaceSideState;
  startTime: number;
  budget: number;
  cerebrasModel: string;
  glmModel: string;
  promptDisplay: string;
  decomposition: LayerInfo[];
  finish?: {
    speedup: number;
    winner: 'cerebras' | 'gpu';
    cerebrasTime: number;
    gpuTime: number;
    cerebrasSuccessful: number;
    cerebrasTotal: number;
    gpuSuccessful: number;
    gpuTotal: number;
  };
  steps: Record<'cerebras' | 'gpu', Record<string, 'pending' | 'active' | 'done'>>;
}

export interface SpeedupBadge {
  text: string;
  variant: 'cerebras' | 'gpu';
}
