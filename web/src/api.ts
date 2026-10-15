// Thin client over the Alchemist REST API. Every screen goes through here; nothing else calls fetch.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body: unknown = {}) => request<T>("POST", path, body),
};

export const fileUrl = (runId: string, path: string, download = false) =>
  `/api/runs/${runId}/files/${path}${download ? "?download=true" : ""}`;

// ---- types (mirror src/alchemist/api)

export interface Me {
  id: string;
  name: string;
  email?: string | null;
  plants: string[];
  global: boolean;
  admin: boolean;
  catalog_admin: boolean;
  workspaces: string[];
  readable_plants: string[];
}

export interface Meta {
  version: string;
  auth: string;
  models: { lead: string; sub: string; allowed_providers: string[]; scripted: boolean };
  budgets: { max_steps: number; max_output_tokens: number };
  tools: { planning: Tool[]; analysis: Tool[] };
  prompts: string;
  plugins: (string | { name: string; version?: string | null })[];
  launcher: string;
  options: { data_formats: string[]; card_drafters: string[] };
}

export interface Tool {
  name: string;
  description: string;
}

export interface Workspace {
  name: string;
  plants: string[];
  projects: { name: string; owner?: string; plants: string[]; created_at?: string }[];
}

export interface RunSummary {
  run_id: string;
  question_id?: string;
  workspace: string;
  project: string;
  user_id: string;
  mode: string;
  parent_run?: string | null;
  status: string;
  created_at?: string;
  updated_at?: string;
  sealed?: boolean;
  plants?: string[];
  entitled: boolean;
  question?: string | null;
}

export interface Hypothesis {
  id: string;
  cause: string;
  method: string;
  datasets?: string[];
  source?: string;
}

export interface Plan {
  version?: number;
  metric?: string;
  scope?: Record<string, unknown>;
  hypotheses: Hypothesis[];
  notes?: string;
  approved_by?: string;
  approved_at?: string;
  submitted_at?: string;
}

export interface Finding {
  id: string;
  hypothesis?: string;
  statement: string;
  value: unknown;
  unit?: string;
  verdict: string;
  confidence: string;
  evidence?: string[];
  value_sha256?: string;
  matches_original?: boolean;
  cell?: string;
}

export interface Query {
  id: string;
  sql: string;
  purpose: string;
  datasets: string[];
  objects: { key: string; etag: string; size: number; last_modified: string; dataset: string }[];
  rows: number;
  snapshot: string;
  result_sha256: string;
  card_versions: Record<string, number>;
}

export interface RunDetail {
  entitled: boolean;
  run: RunSummary;
  question?: { id: string; text: string; asker: string; scope: Record<string, unknown>; status: string; created_at: string };
  plan?: Plan | null;
  permissions?: { approve: boolean; stop: boolean; replay: boolean; verdict: boolean };
  conclusion_md?: string | null;
  conclusion_html?: string | null;
  findings?: Finding[];
  queries?: Query[];
  datasets?: { dataset: string; card_version: number; plant: string; format: string; columns: { name: string; exposure: string }[] }[];
  knowledge?: { citations: { doc: string; version: string; title: string; section: string; stale: boolean }[]; structured: { name: string }[] };
  review?: { ok: boolean; issues: { finding?: string; problem: string; suggestion?: string }[]; notes: string; model: string } | null;
  result_check?: { status: { mode: string; compared: number; mismatches: number }; findings?: unknown[] } | null;
  manifest?: Record<string, any> | null;
  has_report?: boolean;
  has_notebook?: boolean;
  figures?: string[];
  children?: RunSummary[];
  verdicts?: { at: string; actor: string; verdict: string; note: string; findings: Record<string, string> }[];
  failure?: { at: string; error: string } | null;
}

export interface Step {
  phase?: string;
  at: number;
  type: "model" | "tool";
  tool?: string;
  input?: Record<string, unknown>;
  is_error?: boolean;
  seconds?: number;
  output?: string;
  images?: number;
  model?: string;
  stop_reason?: string;
  text?: string;
  usage?: { input_tokens: number; output_tokens: number };
}

export interface NotebookCell {
  type: "code" | "markdown";
  source: string;
  meta: { cell_id?: string; kind?: string; purpose?: string; query_id?: string; finding_id?: string };
  outputs: { type: "text" | "image" | "error"; text?: string; png?: string }[];
}

export interface DatasetSummary {
  dataset: string;
  view: string;
  card_version: number;
  plant: string;
  description?: string;
  grain?: string;
  time_zone?: string;
  time_meaning?: string;
  join_keys?: string[];
  quirks?: string[];
  columns: { name: string; type?: string; unit?: string; description?: string; valid_min?: number; valid_max?: number }[];
  missing_facts?: string[];
}

export const TERMINAL = new Set(["completed", "partial", "failed", "mismatch"]);
