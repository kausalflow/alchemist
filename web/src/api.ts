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
  put: <T,>(path: string, body: unknown = {}) => request<T>("PUT", path, body),
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
  models: { roles: Record<ModelRoleName, string>; allowed_providers: string[]; scripted: boolean };
  budgets: { max_steps: number; max_output_tokens: number };
  tools: { planning: Tool[]; analysis: Tool[] };
  prompts: string;
  plugins: (string | { name: string; version?: string | null })[];
  launcher: string;
  options: { data_formats: string[]; card_drafters: string[] };
}

export type ModelRoleName = "planning" | "analysis" | "reviewer" | "small";

/** A role's model now (GET /admin/models): an admin's choice, or the deployment's configured model. */
export interface ModelRole {
  role: ModelRoleName;
  label: string;
  does: string;
  env: string;
  legacy_env: string;
  spec: string;
  source: "admin" | "configuration";
  configured: string;
  configured_from: string;
  default: string;
  updated_by: string | null;
  updated_at: string | null;
  ignored: { spec: string; reason: string } | null;
}

export interface ModelSettings {
  roles: ModelRole[];
  allowed_providers: string[];
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
  revises?: number;
}

/** A note a person left for the agent, and when the agent received it (null: not yet, or the run ended first). */
export interface RunNote {
  id: number;
  n: number;
  at: string;
  author: string;
  text: string;
  kind: "note" | "send_back";
  status?: string;
  delivered: { phase: string; step: number | null; model_call: number | null; at: string } | null;
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
  permissions?: {
    approve: boolean;
    send_back?: boolean;
    note?: boolean;
    stop: boolean;
    pause?: boolean;
    resume?: boolean;
    replay: boolean;
    verdict: boolean;
  };
  notes?: RunNote[];
  plan_history?: Plan[];
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
  closed?: { at: string; actor: string; reason: string } | null;
}

export interface Step {
  phase?: string;
  at: number;
  type: "model" | "tool" | "note";
  notes?: { n: number; author: string; kind: string; text: string; at: string }[];
  meta?: { figures?: string[]; cell?: string; preview?: QueryPreview };
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

/** A query result's shape as people following the run see it (the model gets its own summary). */
export interface QueryPreview {
  query: string;
  cell?: string;
  rows?: number;
  columns?: Record<string, string>;
  stats?: Record<string, Record<string, unknown>>;
  spark?: Record<string, (number | null)[]>;
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

export interface CardColumn {
  name: string;
  type?: string;
  unit?: string;
  description?: string;
  valid_min?: number;
  valid_max?: number;
  exposure?: "model" | "pseudonymise" | "never_to_model" | string;
}

/** A catalog card as stored (src/alchemist/catalog.py, schema alchemist.card/v1). */
export interface Card {
  schema?: string;
  dataset?: string;
  description?: string;
  grain?: string;
  time_zone?: string;
  time_meaning?: string;
  join_keys?: string[];
  refresh_lag?: string;
  quirks?: string[];
  example_queries?: string[];
  columns?: CardColumn[];
  missing?: string[];
  [k: string]: unknown;
}

export interface CardVersion {
  dataset_id: string;
  version: number;
  status: string;
  drafted_by: string;
  reviewed_by?: string | null;
  card: Card;
  checks: string[];
}

export interface Suggestion {
  id: number;
  dataset_id: string;
  raised_by: string;
  kind: string;
  text: string;
  status: string;
  created_at: string;
}

export interface DatasetDetail {
  dataset: Record<string, string>;
  approved: { version: number; card: Card; reviewed_by: string; reviewed_at: string } | null;
  summary: DatasetSummary | null;
  versions?: CardVersion[];
  suggestions?: Suggestion[];
}

// ---- admin (catalog admins and admins; see the admin API contract)

export interface PlantSummary {
  id: string;
  name: string;
  description: string;
  in_access_model: boolean;
  readers_group: string; // "" for the shared "global" catalog
  datasets: number;
  datasets_waiting: number;
  datasets_unusable: number;
  documents: number;
  documents_overdue: number;
  facts: number;
  lines: number;
}

export interface DatasetRow {
  dataset: string;
  name: string;
  plant: string;
  s3_prefix: string;
  file_glob: string;
  file_format: string;
  metadata_uri: string;
  owner: string;
  approved_version: number | null;
  latest_version: number | null;
  waiting_for_review: boolean;
  missing_facts: string[];
  checks: string[];
  no_metadata_file: boolean;
  runs_using: number | null; // null = unknown
}

export const DOC_TYPES = ["SOP", "FMEA", "Incident report", "Specification", "Design document", "Procedure", "Other"] as const;

export interface DocSummary {
  id: string;
  title: string;
  type: string;
  plant: string;
  lines: string[];
  version: number;
  review_date: string; // "YYYY-MM-DD" or ""
  overdue: boolean;
  updated_at: string;
  updated_by: string;
  sections: number;
  read_only: boolean; // shipped by a plugin knowledge pack
}

export type DocDetail = DocSummary & { content: string };

export interface FactSummary {
  name: string;
  plant: string;
  version: number;
  description: string;
  updated_at: string;
  updated_by: string;
  read_only: boolean;
}

export type FactDetail = FactSummary & { content: string };

export interface Line {
  id: string;
  name: string;
  description: string;
}

export interface PlantDetail {
  plant: PlantSummary;
  datasets: DatasetRow[];
  documents: DocSummary[];
  facts: FactSummary[];
  lines: Line[];
  suggestions: Suggestion[];
}

export interface PlantPreview {
  datasets: { dataset: string; plant: string; description: string; version: number }[];
  documents: { doc: string; title: string; section: string; version: number; plant: string; stale: boolean; text: string }[];
  facts: { name: string; plant: string; version: number }[];
  lines: Line[];
}

export interface DevUser {
  id: string;
  plants: string[];
  workspaces: string[];
  admin: boolean;
  global: boolean;
}

export const TERMINAL = new Set(["completed", "partial", "failed", "mismatch", "cancelled", "expired"]);
/** Terminal statuses that end with a sealed report. */
export const SEALED = new Set(["completed", "partial", "mismatch"]);
/** Terminal statuses that end without one. */
export const CLOSED = new Set(["failed", "cancelled", "expired"]);
