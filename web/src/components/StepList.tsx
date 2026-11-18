import { useEffect, useState, type ReactNode } from "react";
import type { Step } from "../api";
import { Code, Collapsible } from "./bits";
import { QueryPreviewView, StepFigures } from "./Visuals";
import { useSimple } from "../viewMode";

const VERB: Record<string, string> = {
  search_catalog: "Searched the catalog",
  search_knowledge: "Searched plant documents",
  submit_plan: "Submitted the plan",
  run_query: "Queried data",
  run_python: "Ran Python",
  record_finding: "Recorded a finding",
  suggest_catalog_fix: "Suggested a catalog fix",
  finish_analysis: "Asked for an independent review",
  build_report: "Built the report",
};

const KIND: Record<string, string> = {
  run_query: "query",
  run_python: "compute",
  record_finding: "finding",
  search_catalog: "search",
  search_knowledge: "search",
  submit_plan: "plan",
  finish_analysis: "review",
  build_report: "report",
};

/** The badge a step gets in the activity log: query, compute, finding, search, plan, review, report, tool, note
 * (the model's own text) or steer (a note a person left, as the agent received it). */
export const kindOf = (s: Step) => (s.type === "note" ? "steer" : s.type === "model" ? "note" : (KIND[s.tool ?? ""] ?? "tool"));

export function describe(s: Step): string {
  if (s.type === "note")
    return (s.notes ?? []).map((n) => `${n.author} ${n.kind === "send_back" ? "sent the plan back" : "left a note"}: ${n.text}`).join("\n");
  if (s.type === "model") return s.text ?? "";
  const inp = s.input ?? {};
  const purpose = (inp.purpose as string) || (inp.query as string) || (inp.statement as string) || (inp.summary as string) || "";
  const h = typeof inp.hypothesis === "string" ? `${inp.hypothesis}: ` : "";
  const verb = VERB[s.tool ?? ""] ?? (s.tool?.startsWith("penguineer_") ? `Ran a statistics check (${s.tool.slice(11).replace(/_/g, " ")})` : s.tool);
  return `${verb}${purpose ? `: ${h}${purpose}` : ""}`;
}

/** How a note names the step it is about, so the agent knows which query, calculation or figure is meant. */
const stepRef = (n: number, s: Step) => {
  const what = describe(s);
  return `About step #${n} (${what.length > 90 ? `${what.slice(0, 87)}...` : what}): `;
};

/** True once the agent has handed its findings to the reviewer (or is building the report). */
export const isReviewing = (steps: Step[]) => steps.some((s) => s.tool === "finish_analysis" || s.tool === "build_report");

/** Plain words for the badges in the simple view. */
const SIMPLE_KIND: Record<string, string> = { note: "agent", steer: "you", compute: "calculation", query: "data", tool: "step" };

/** Model turns and tool calls as an activity log, newest last. Code and SQL are shown in full; outputs are what the model
 * saw. With `runId`, figures and query previews are shown under the step that made them. The simple view leaves out
 * code, SQL, tool names and raw results, and keeps the story, figures and previews. */
export default function StepList({
  steps,
  live = false,
  liveText,
  runId,
  onComment,
}: {
  steps: Step[];
  live?: boolean;
  liveText?: string;
  runId?: string;
  /** Offered on each step while the run takes notes: start a note to the agent about this step. */
  onComment?: (ref: string) => void;
}) {
  const simple = useSimple();
  const shown = steps.filter((s) => s.type !== "model" || (s.text && s.text.trim()));
  if (!shown.length && !live) return <p className="muted pad">No steps recorded.</p>;
  let n = 0;
  const nums = shown.map((s) => (s.type === "tool" ? ++n : n));
  const items = groupDebugging(shown);
  const row = (i: number) => {
        const s = shown[i];
        const kind = kindOf(s);
        const num = nums[i];
        const sql = typeof s.input?.sql === "string" ? (s.input.sql as string).trim() : null;
        const code = typeof s.input?.code === "string" ? (s.input.code as string) : null;
        return (
          <li
            key={i}
            id={s.type === "tool" && runId ? `step-${num}` : undefined}
            className={`log-row ${s.is_error ? "err" : ""} ${kind === "note" || kind === "steer" ? kind : ""}`}
          >
            <span className="mono faint small">{s.type === "tool" ? `#${num}` : ""}</span>
            <span className={`kind k-${kind}`}>
              {simple ? (SIMPLE_KIND[kind] ?? kind) : kind === "note" ? "model" : kind === "steer" ? "person" : kind}
            </span>
            <div className="log-body">
              <div className={kind === "note" ? "muted" : kind === "steer" ? "steer-text" : ""}>
                {s.type === "model" && s.html ? (
                  // Sanitised on the server (safehtml.py), the same way as the conclusion.
                  <div className="step-md" dangerouslySetInnerHTML={{ __html: s.html }} />
                ) : (
                  describe(s)
                )}
                {s.is_error && (
                  <>
                    {" "}
                    <span className={`pill ${simple ? "warn" : "bad"}`}>{simple ? "did not work, retried" : "error"}</span>
                  </>
                )}
              </div>
              {!simple && sql && <Code lang="sql">{sql}</Code>}
              {!simple && code && <Code lang="python">{code}</Code>}
              {runId && s.meta?.preview && <QueryPreviewView p={s.meta.preview} />}
              {runId && !!s.meta?.figures?.length && <StepFigures runId={runId} figures={s.meta.figures} />}
              {!simple && s.type === "tool" && (s.tool === "record_finding" || s.tool === "submit_plan" || s.tool === "build_report" || s.output) && (
                <Collapsible title={<span className="small">{s.output ? "Input and result" : "Input"}</span>}>
                  {(s.tool === "record_finding" || s.tool === "submit_plan" || s.tool === "build_report") && (
                    <Code>{JSON.stringify(s.input, null, 2)}</Code>
                  )}
                  {s.output && (
                    <>
                      <div className="faint small">Result returned to the model</div>
                      <Code>{s.output}</Code>
                    </>
                  )}
                </Collapsible>
              )}
              {simple && s.type === "tool" && s.at ? <div className="faint tiny">{clock(s.at)}</div> : null}
              {onComment && s.type === "tool" && (
                <button type="button" className="link small step-comment" onClick={() => onComment(stepRef(num, s))}>
                  Comment on this step
                </button>
              )}
              {!simple && s.type === "tool" && (
                <div className="mono faint tiny">
                  {s.tool}
                  {s.seconds !== undefined && s.seconds !== null && ` · ${s.seconds}s`}
                  {s.at ? ` · ${clock(s.at)}` : ""}
                </div>
              )}
            </div>
          </li>
        );
  };
  return (
    <ol className="log">
      {items.map((it) =>
        typeof it === "number" ? (
          row(it)
        ) : (
          <DebugGroup key={`g${it[0]}`} rows={it} steps={shown} nums={nums} simple={simple} working={live && it[it.length - 1] === shown.length - 1}>
            {it.map(row)}
          </DebugGroup>
        ),
      )}
      {live && (
        <li className="log-live" aria-live="polite">
          <span className="live-dot" aria-hidden="true" />
          {liveText ?? (shown.length ? "Working…" : "Waiting for the first step…")}
        </li>
      )}
    </ol>
  );
}

/** Indexes of the steps, with each stretch of debugging as one group: a step that did not work, and what follows it
 * until a step works again (more failed attempts and the agent's notes on fixing them). A person's note ends a
 * stretch, so it is never hidden. */
export function groupDebugging(steps: Step[]): (number | number[])[] {
  const out: (number | number[])[] = [];
  let group: number[] | null = null;
  steps.forEach((s, i) => {
    if (group && (s.type === "model" || (s.type === "tool" && s.is_error))) group.push(i);
    else if (s.type === "tool" && s.is_error) out.push((group = [i]));
    else {
      group = null;
      out.push(i);
    }
  });
  // Notes the agent wrote after its last failed attempt belong to the steps that follow, not to the debugging.
  return out.flatMap((g) => {
    if (typeof g === "number") return [g];
    let end = g.length;
    while (end > 1 && steps[g[end - 1]].type === "model") end -= 1;
    return [g.slice(0, end), ...g.slice(end)];
  });
}

/** A stretch of debugging, folded to one line until someone opens it: most readers only need to know that a step
 * failed and the agent fixed it; the attempts themselves are for advanced users. */
function DebugGroup({
  rows,
  steps,
  nums,
  simple,
  working,
  children,
}: {
  rows: number[];
  steps: Step[];
  nums: number[];
  simple: boolean;
  working: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const failed = rows.filter((i) => steps[i].type === "tool");
  const first = nums[failed[0]];
  const last = nums[failed[failed.length - 1]];
  const label = `${failed.length} attempt${failed.length === 1 ? "" : "s"} that did not work`;
  return (
    <li className={`log-debug ${open ? "open" : ""}`}>
      <div className="log-row debug-head">
        <span className="mono faint small">{first === last ? `#${first}` : `#${first}–${last}`}</span>
        <span className="kind k-debug">{simple ? "retry" : "debug"}</span>
        <div className="log-body">
          <div className="muted">
            {label}
            {working ? ". The agent is fixing it now." : ". The agent fixed it and carried on."}
          </div>
          <button type="button" className="link small" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Hide the debugging" : `Show the debugging (${rows.length} step${rows.length === 1 ? "" : "s"})`}
          </button>
        </div>
      </div>
      {open && <ol className="log debug-body">{children}</ol>}
    </li>
  );
}

/** Transcript times are epoch seconds. */
export function clock(at: number) {
  const d = new Date(at * 1000);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** What a working run does right after each kind of step, in plain words. */
const NEXT: Record<string, string> = {
  search_catalog: "Looking through the data catalog for data that can answer the question",
  search_knowledge: "Reading plant documents and past investigations",
  submit_plan: "Finishing the plan",
  run_query: "Looking at the data it just read",
  run_python: "Looking at the result of a calculation",
  record_finding: "Writing down what it found and moving to the next cause",
  finish_analysis: "A second model is checking the findings",
  build_report: "Writing the report",
};

/** One line on what a planning or running question is doing now, how long ago the last step was, and how far along
 * it is, so a person glancing at the page knows it is alive. */
export function NowLine({
  steps,
  phase,
  paused = false,
  reviewing = false,
  causes,
}: {
  steps: Step[];
  phase: "planning" | "running";
  paused?: boolean;
  reviewing?: boolean;
  causes?: { done: number; total: number };
}) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 20000);
    return () => clearInterval(t);
  }, []);
  const tools = steps.filter((s) => s.type === "tool");
  const last = tools[tools.length - 1];
  const lastAny = steps[steps.length - 1];
  let main: string;
  if (paused) main = "Paused";
  else if (reviewing) main = "A second model is checking the findings before the report is written";
  else if (lastAny?.type === "note") main = "Reading your note";
  else if (!last)
    main = phase === "planning" ? "Reading your question and looking for data that could answer it" : "Setting up the notebook and data";
  else main = NEXT[last.tool ?? ""] ?? (phase === "planning" ? "Drafting the plan" : "Deciding the next step");
  const ago = lastAny?.at ? Math.max(0, Math.round((Date.now() / 1000 - lastAny.at) / 60)) : null;
  const bits = [
    tools.length ? `${tools.length} step${tools.length === 1 ? "" : "s"} so far` : null,
    causes && causes.total ? `findings for ${causes.done} of ${causes.total} causes` : null,
    ago !== null ? (ago < 1 ? "last step just now" : `last step ${ago} min ago`) : null,
  ].filter(Boolean);
  return (
    <div className="now-line" aria-live="polite">
      {!paused && <span className="live-dot" aria-hidden="true" />}
      <span className="now-main">{main}{paused ? "" : "…"}</span>
      {bits.length > 0 && <span className="now-sub">{bits.join(" · ")}</span>}
    </div>
  );
}
