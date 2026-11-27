export interface GuardPolicy {
  auto_stop_minutes: number;
  idle_stop_minutes: number;
  balance_floor_ccu: number;
  session_spend_cap_ccu: number;
  max_vram_retries: number;
}

export interface AppSettings {
  backend_mode: "mock" | "real";
  default_accelerator: string;
  auto_forward_endpoints: boolean;
  auto_adopt_watchdog: boolean;
  guards: GuardPolicy;
}

export interface AccountUsage {
  status: "live" | "stale" | "unknown";
  balance_ccu: number | null;
  burn_rate_hourly: number | null;
  assignments_count: number | null;
  eligible_gpus: string[];
  fetched_at: string | null;
  stale_reason: string | null;
}

export interface AccountInfo {
  id: string;
  email: string;
  display_name: string;
  added_at: string;
  is_active: boolean;
  has_credential: boolean;
  usage: AccountUsage;
}

export interface TunnelInfo {
  id: string;
  session_name: string;
  remote_port: number;
  local_port: number;
  label: string;
  path: string;
  status: string;
  local_url: string;
  error: string | null;
}

export interface RuntimeSession {
  name: string;
  endpoint: string;
  account_id: string;
  account_email: string;
  accelerator: string;
  gpu_name: string | null;
  vram_total_mib: number | null;
  vram_used_mib: number | null;
  gpu_util_percent: number | null;
  managed: boolean;
  adopted: boolean;
  status: string;
  created_at: string | null;
  estimated_burn_hourly: number;
  runtime_url: string | null;
  watchdog: {
    active: boolean;
    deadline_epoch: number | null;
    idle_timeout_s: number | null;
    warning: string | null;
  };
  tunnels: TunnelInfo[];
}

export interface RecipeInput {
  id: string;
  type: "text" | "number" | "bool" | "select" | "secret";
  label: string;
  help: string;
  default: unknown;
  options: string[];
  min?: number | null;
  max?: number | null;
}

export interface RecipeSpec {
  id: string;
  name: string;
  description: string;
  version: string;
  category: string;
  tags: string[];
  default_accelerator: string;
  min_vram_mib: number;
  high_mem: boolean;
  inputs: RecipeInput[];
  stages: { id: string; label: string }[];
  endpoints: { id: string; label: string; port?: number | null; port_input?: string | null; path: string }[];
  output_dir: string;
}

export interface DeploymentRun {
  run_id: string;
  recipe_id: string;
  recipe_name: string;
  session_name: string;
  accelerator: string;
  state: "queued" | "provisioning" | "running" | "ready" | "failed" | "cancelled";
  stage: string;
  progress: number;
  message: string;
  logs: string[];
  endpoints: TunnelInfo[];
  started_at: string;
  finished_at: string | null;
}

export interface HarvestArtifact {
  id: string;
  filename: string;
  local_path: string;
  remote_path: string;
  session_name: string;
  size_bytes: number;
  sha256: string;
  media_type: "video" | "image" | "other";
  created_at: string;
}

export interface BatchShotItem {
  index: number;
  prompt: string;
  subtitle: string;
  seed: number;
  state: "pending" | "running" | "done" | "failed";
  artifact_id: string | null;
  error: string | null;
}

export interface BatchJob {
  job_id: string;
  title: string;
  session_name: string;
  workflow_mode: "wan21_builtin" | "custom_json";
  width: number;
  height: number;
  frames: number;
  steps: number;
  auto_stitch: boolean;
  burn_subtitles: boolean;
  auto_stop: boolean;
  state: "queued" | "running" | "composing" | "done" | "failed";
  progress: number;
  message: string;
  shots: BatchShotItem[];
  final_artifact_id: string | null;
  srt_path: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface UnassignReceipt {
  receipt_id: string;
  timestamp: string;
  account_email: string;
  session_name: string;
  endpoint: string;
  accelerator: string;
  reason: string;
  duration_seconds: number | null;
  estimated_ccu_spent: number;
  balance_after_ccu: number | null;
  verified_unassigned: boolean;
  backend_mode: string;
}

export interface BootstrapData {
  settings: AppSettings;
  accounts: AccountInfo[];
  active_account: AccountInfo | null;
  secret_names: string[];
  sessions: RuntimeSession[];
  recipes: RecipeSpec[];
  runs: DeploymentRun[];
  batch_jobs: BatchJob[];
  artifacts: HarvestArtifact[];
  receipts: UnassignReceipt[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  bootstrap: () => req<BootstrapData>("/api/bootstrap"),
  patchSettings: (body: Record<string, unknown>) =>
    req<AppSettings>("/api/settings", { method: "PATCH", body: JSON.stringify(body) }),
  oauthStart: () => req<{ flow_id: string; auth_url: string }>("/api/accounts/oauth/start", { method: "POST" }),
  oauthComplete: (flow_id: string, auth_code: string) =>
    req<AccountInfo>("/api/accounts/oauth/complete", { method: "POST", body: JSON.stringify({ flow_id, auth_code }) }),
  activateAccount: (id: string) => req<{ ok: boolean }>(`/api/accounts/${id}/activate`, { method: "POST" }),
  deleteAccount: (id: string) => req<{ ok: boolean }>(`/api/accounts/${id}`, { method: "DELETE" }),
  saveSecret: (name: string, value: string) =>
    req<{ secret_names: string[] }>("/api/secrets", { method: "POST", body: JSON.stringify({ name, value }) }),
  deleteSecret: (name: string) => req<{ secret_names: string[] }>(`/api/secrets/${name}`, { method: "DELETE" }),
  adoptSession: (endpoint: string) =>
    req<RuntimeSession>("/api/sessions/adopt", { method: "POST", body: JSON.stringify({ endpoint }) }),
  extendSession: (name: string, minutes = 30) =>
    req("/api/sessions/" + encodeURIComponent(name) + "/extend", { method: "POST", body: JSON.stringify({ minutes }) }),
  harvestSession: (name: string, auto_stop = false) =>
    req<{ harvested: HarvestArtifact[]; receipt: UnassignReceipt | null }>(
      "/api/sessions/" + encodeURIComponent(name) + "/harvest",
      { method: "POST", body: JSON.stringify({ auto_stop }) },
    ),
  stopSession: (name: string) =>
    req<UnassignReceipt>("/api/sessions/" + encodeURIComponent(name) + "/stop", { method: "POST" }),
  stopAll: () =>
    req<{ stopped_count: number; remaining_sessions: number; zero_burn_verified: boolean }>("/api/sessions/stop-all", {
      method: "POST",
    }),
  deploy: (payload: {
    recipe_id: string;
    accelerator?: string;
    min_vram_mib?: number;
    session_name?: string;
    inputs?: Record<string, unknown>;
  }) => req<DeploymentRun>("/api/deployments", { method: "POST", body: JSON.stringify(payload) }),
  createBatch: (payload: {
    title: string;
    session_name: string;
    shots: { prompt: string; subtitle: string; seed?: number }[];
    workflow_mode: "wan21_builtin" | "custom_json";
    custom_workflow_json?: string;
    width: number;
    height: number;
    frames: number;
    steps: number;
    auto_stitch: boolean;
    burn_subtitles: boolean;
    auto_stop: boolean;
  }) => req<BatchJob>("/api/batch", { method: "POST", body: JSON.stringify(payload) }),
  compose: (payload: {
    artifact_ids: string[];
    subtitles: string[];
    title: string;
    burn_subtitles: boolean;
  }) => req<{ artifact: HarvestArtifact; srt_path: string | null }>("/api/compose", { method: "POST", body: JSON.stringify(payload) }),
};
