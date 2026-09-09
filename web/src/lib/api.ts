/**
 * Typed client for the Helve observability API.
 *
 * Auth: the server generates a bearer token into ~/.helve/auth_token, which a
 * browser cannot read.  In production the server injects it into the console's
 * HTML; in dev the user pastes it once and it lives in localStorage.  Embedding
 * it in a localhost page adds no exposure that matters — CORS already limits
 * the API to localhost origins, so no other site can read the response, and any
 * local process running as this user could read the token file directly anyway.
 */

const TOKEN_STORAGE_KEY = "helve.console.token";

declare global {
  interface Window {
    __HELVE_TOKEN__?: string;
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function readToken(): string | null {
  if (window.__HELVE_TOKEN__) return window.__HELVE_TOKEN__;
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    // A browser with site data blocked still works, it just asks every time.
    return null;
  }
}

export function storeToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_STORAGE_KEY, token.trim());
  } catch {
    window.__HELVE_TOKEN__ = token.trim();
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* nothing to clear */
  }
  window.__HELVE_TOKEN__ = undefined;
}

// The observability routes live under the agent router's prefix; /api/health
// is the one endpoint mounted at the root.  Verified against the live
// openapi.json rather than assumed -- the first version of this client guessed
// /api and got a 404 on everything.
const AGENT_PREFIX = "/api/agent";

async function request<T>(path: string, prefix: string = AGENT_PREFIX): Promise<T> {
  const token = readToken();
  const response = await fetch(`${prefix}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    // 401 is the one a person can act on, so it says what to do rather than
    // repeating the status line.
    const message =
      response.status === 401
        ? "The API rejected this token. Run `cat ~/.helve/auth_token` and paste it again."
        : `${response.status} ${response.statusText} on ${path}`;
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

// ── the shapes the server actually returns ─────────────────────────────────

export interface RunSummary {
  run_id: string;
  agent_id: string;
  session_id: string | null;
  identity_id: string | null;
  route_id: string | null;
  pipeline_id: string | null;
  working_dir: string | null;
  forced_skill: string | null;
  created_at: string;
  finished_at: string;
  outcome: string | null;
  reason: string | null;
  event_count: number;
  event_counts: Record<string, number>;
  tool_call_count: number;
  backtrack_count: number;
  approval_required_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface TraceEvent {
  seq: number;
  timestamp: string;
  run_id: string;
  type: string;
  data: Record<string, unknown>;
}

export interface Incident {
  run_id: string;
  agent_id: string;
  severity: string;
  category: string;
  message: string;
  reason: string | null;
  occurred_at: string;
  evidence: Record<string, string | number>;
}

export interface Diagnosis {
  run_id: string;
  agent_id: string;
  status: string;
  failure_node: string | null;
  primary_category: string | null;
  summary: string;
  evidence: string[];
  recommendation: string | null;
}

export interface AgentHealth {
  agent_id: string;
  run_count: number;
  completed_count: number;
  unsuccessful_count: number;
  success_rate: number;
  tool_call_count: number;
  tool_success_rate: number | null;
  average_backtracks: number;
  total_tokens: number;
  tokens_per_run: number;
}

export interface ImprovementProposal {
  run_id: string;
  agent_id: string;
  status: string;
  category: string | null;
  title: string;
  rationale: string;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/health", "/api"),
  runs: (limit = 50) => request<RunSummary[]>(`/observability/runs?limit=${limit}`),
  run: (runId: string) => request<RunSummary>(`/observability/runs/${runId}`),
  trace: (runId: string) => request<TraceEvent[]>(`/observability/runs/${runId}/trace`),
  diagnosis: (runId: string) => request<Diagnosis>(`/observability/runs/${runId}/diagnosis`),
  proposal: (runId: string) =>
    request<ImprovementProposal>(`/observability/runs/${runId}/improvement-proposal`),
  incidents: () => request<Incident[]>("/observability/incidents"),
  // One object, not a list: the route declares response_model=AgentHealthOut.
  // Treating it as an array made .map() throw and unmounted the whole tree.
  agentHealth: () => request<AgentHealth>("/observability/health"),
};
