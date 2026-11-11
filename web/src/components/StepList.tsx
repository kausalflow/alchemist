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
  return (
    <ol className="log">
      {shown.map((s, i) => {
        const kind = kindOf(s);
        if (s.type === "tool") n += 1;
        const num = n;
        const sql = typeof s.input?.sql === "string" ? (s.input.sql as string).trim() : null;
        const code = typeof s.input?.code === "string" ? (s.input.code as string) : null;
        return (
          <li
            key={i}
            id={s.type === "tool" && runId ? `step-${n}` : undefined}
            className={`log-row ${s.is_error ? "err" : ""} ${kind === "note" || kind === "steer" ? kind : ""}`}
          >
            <span className="mono faint small">{s.type === "tool" ? `#${n}` : ""}</span>
            <span className={`kind k-${kind}`}>
              {simple ? (SIMPLE_KIND[kind] ?? kind) : kind === "note" ? "model" : kind === "steer" ? "person" : kind}
            </span>
            <div className="log-body">
              <div className={kind === "note" ? "muted" : kind === "steer" ? "steer-text" : ""}>
                {describe(s)}
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
      })}
      {live && (
        <li className="log-live" aria-live="polite">
          <span className="live-dot" aria-hidden="true" />
          {liveText ?? (shown.length ? "Working…" : "Waiting for the first step…")}
        </li>
      )}
    </ol>
  );
}

/** Transcript times are epoch seconds. */
export function clock(at: number) {
  const d = new Date(at * 1000);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
