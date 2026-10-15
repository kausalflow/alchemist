import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { RunSummary } from "../api";
import { useApi, useSession } from "../session";
import { useWorkspace } from "../workspace";
import { ErrorNote, relTime, shortId, statusLabel } from "../components/bits";
import Icon from "../components/icons";
import { WaitingCards, waitingOnMe } from "../components/Waiting";

interface Column {
  id: string;
  name: string;
  tone: string;
  hint: string;
  statuses: string[];
}

const COLUMNS: Column[] = [
  { id: "plan", name: "Drafting plan", tone: "plan", hint: "The agent reads the catalog and plant documents. No data is read yet.", statuses: ["planning"] },
  {
    id: "approve",
    name: "Needs approval",
    tone: "warn",
    hint: "The owner checks causes, data and methods. Plans expire after 14 days.",
    statuses: ["awaiting_approval"],
  },
  {
    id: "run",
    name: "Running",
    tone: "info",
    hint: "Every query and computation becomes a notebook cell; a second model reviews the findings at the end.",
    statuses: ["running", "paused"],
  },
  { id: "sealed", name: "Sealed", tone: "ok", hint: "Report, data snapshots and notebook are fixed. Replay or rerun.", statuses: ["completed", "partial", "mismatch"] },
  { id: "closed", name: "Closed", tone: "", hint: "Failed, cancelled, or the plan expired before anyone approved it.", statuses: ["failed", "cancelled", "expired"] },
];

const ACTIVE = new Set(["planning", "running"]);

export default function BoardPage() {
  const { me } = useSession();
  const { current, ws } = useWorkspace();
  const [params] = useSearchParams();
  const project = params.get("project") ?? "";
  const [who, setWho] = useState<"mine" | "all">("all");
  const [q, setQ] = useState("");
  const [showClosed, setShowClosed] = useState(false);

  const qs = new URLSearchParams();
  if (current) qs.set("workspace", current);
  if (project) qs.set("project", project);
  const { data: runs, error, reload } = useApi<RunSummary[]>(`/runs?${qs}`);

  // While something is drafting or running, refresh the board now and then so cards move by themselves.
  const busy = runs?.some((r) => ACTIVE.has(r.status));
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(reload, 15000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (runs ?? []).filter(
      (r) => (who === "all" || r.user_id === me!.id) && (!needle || (r.question ?? "").toLowerCase().includes(needle) || r.run_id.includes(needle)),
    );
  }, [runs, who, q, me]);
  const waiting = runs ? waitingOnMe(runs, me!) : [];
  const cols = COLUMNS.filter((c) => c.id !== "closed" || showClosed);
  const closedCount = shown.filter((r) => COLUMNS[4].statuses.includes(r.status)).length;

  return (
    <div className="page">
      <header className="page-head">
        <div className="grow">
          <div className="crumb">
            {current || "All workspaces"}
            {project && <> / <span className="mono">{project}</span></>}
          </div>
          <h1>{project || (ws ? `${ws.name} questions` : "Questions")}</h1>
        </div>
        <div className="seg" role="group" aria-label="Show">
          <button type="button" aria-pressed={who === "mine"} onClick={() => setWho("mine")}>
            Mine
          </button>
          <button type="button" aria-pressed={who === "all"} onClick={() => setWho("all")}>
            Everyone's
          </button>
        </div>
        <label className="searchbox">
          <Icon name="search" />
          <span className="sr-only">Search questions</span>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search questions" />
        </label>
      </header>

      <ErrorNote error={error} />

      {waiting.length > 0 && (
        <section aria-labelledby="wait" className="section">
          <h2 id="wait" className="eyebrow warn-text">
            Waiting on you
          </h2>
          <WaitingCards runs={waiting} />
        </section>
      )}

      <section aria-label="Questions by stage" className="section board-wrap">
        <div className="board-tools">
          <span className="muted small">
            {runs ? `${shown.length} ${shown.length === 1 ? "run" : "runs"}` : "Loading…"}
          </span>
          <button type="button" className="ghost small" aria-pressed={showClosed} onClick={() => setShowClosed(!showClosed)}>
            {showClosed ? "Hide closed" : `Show closed (${closedCount})`}
          </button>
        </div>
        <div className="board" style={{ gridTemplateColumns: `repeat(${cols.length}, minmax(230px, 1fr))` }}>
          {cols.map((c) => {
            const cards = shown.filter((r) => c.statuses.includes(r.status));
            return (
              <div key={c.id} className="board-col">
                <div className="col-head">
                  <span className={`sdot ${c.tone}`} aria-hidden="true" />
                  <h3>{c.name}</h3>
                  <span className="mono faint small push">{cards.length}</span>
                </div>
                <p className="faint small">{c.hint}</p>
                {cards.map((r) => (
                  <RunCard key={r.run_id} run={r} mine={r.user_id === me!.id} />
                ))}
                {runs && cards.length === 0 && <p className="faint small empty">Nothing here.</p>}
              </div>
            );
          })}
        </div>
        {runs && runs.length === 0 && (
          <p className="muted">
            No questions here yet. <Link to="/ask">Ask one</Link> to start.
          </p>
        )}
      </section>
    </div>
  );
}

function RunCard({ run: r, mine }: { run: RunSummary; mine: boolean }) {
  const [foot, tone] = footer(r);
  return (
    <Link to={`/runs/${r.run_id}`} className={`run-card ${mine && r.status === "awaiting_approval" ? "hot" : ""} ${r.entitled ? "" : "restricted"}`}>
      <div className="run-card-top mono">
        <span>run {shortId(r.run_id)}</span>
        <span>{mine ? "you" : r.user_id}</span>
      </div>
      <div className="run-card-q">{r.question ?? r.run_id}</div>
      <div className={`small ${tone}`}>{foot}</div>
    </Link>
  );
}

function footer(r: RunSummary): [string, string] {
  if (!r.entitled) return ["Uses plant data you cannot read: title and status only", "faint"];
  const kind = r.mode === "replay" ? "Replay" : r.mode === "rerun" ? "Rerun" : "";
  const plants = r.plants?.length ? ` · ${r.plants.join(", ")}` : "";
  const since = relTime(r.updated_at ?? r.created_at);
  switch (r.status) {
    case "planning":
      return [`Reading the catalog and documents · started ${relTime(r.created_at)}`, "plan-text"];
    case "awaiting_approval":
      return [`Plan ready ${since}${plants}`, "warn-text"];
    case "running":
      return [`${kind ? `${kind} running` : "Analysing"} · started ${relTime(r.created_at)}`, "info-text"];
    case "paused":
      return [`Paused ${since}`, "warn-text"];
    case "completed":
      return [`${kind ? `${kind} · ` : ""}Sealed ${since}${plants}`, "ok-text"];
    case "partial":
      return [`Partial report · sealed ${since}`, "muted"];
    case "mismatch":
      return [`${kind || "Replay"} did not match the original`, "bad-text"];
    default:
      return [`${statusLabel(r.status)} ${since}`, "faint"];
  }
}
