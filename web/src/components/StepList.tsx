import type { Step } from "../api";
import { Code, Collapsible } from "./bits";
import { QueryPreviewView, StepFigures } from "./Visuals";

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
  return `${VERB[s.tool ?? ""] ?? s.tool}${purpose ? `: ${h}${purpose}` : ""}`;
}

/** True once the agent has handed its findings to the reviewer (or is building the report). */
export const isReviewing = (steps: Step[]) => steps.some((s) => s.tool === "finish_analysis" || s.tool === "build_report");

/** Model turns and tool calls as an activity log, newest last. Code and SQL are shown in full; outputs are what the model
 * saw. With `runId`, figures and query previews are shown under the step that made them. */
export default function StepList({
  steps,
  live = false,
  liveText,
  runId,
}: {
  steps: Step[];
  live?: boolean;
  liveText?: string;
  runId?: string;
}) {
  const shown = steps.filter((s) => s.type !== "model" || (s.text && s.text.trim()));
  if (!shown.length && !live) return <p className="muted pad">No steps recorded.</p>;
  let n = 0;
  return (
    <ol className="log">
      {shown.map((s, i) => {
        const kind = kindOf(s);
        if (s.type === "tool") n += 1;
        const sql = typeof s.input?.sql === "string" ? (s.input.sql as string).trim() : null;
        const code = typeof s.input?.code === "string" ? (s.input.code as string) : null;
        return (
          <li
            key={i}
            id={s.type === "tool" && runId ? `step-${n}` : undefined}
            className={`log-row ${s.is_error ? "err" : ""} ${kind === "note" || kind === "steer" ? kind : ""}`}
          >
            <span className="mono faint small">{s.type === "tool" ? `#${n}` : ""}</span>
            <span className={`kind k-${kind}`}>{kind === "note" ? "model" : kind === "steer" ? "person" : kind}</span>
            <div className="log-body">
              <div className={kind === "note" ? "muted" : kind === "steer" ? "steer-text" : ""}>
                {describe(s)}
                {s.is_error && (
                  <>
                    {" "}
                    <span className="pill bad">error</span>
                  </>
                )}
              </div>
              {sql && <Code lang="sql">{sql}</Code>}
              {code && <Code lang="python">{code}</Code>}
              {runId && s.meta?.preview && <QueryPreviewView p={s.meta.preview} />}
              {runId && !!s.meta?.figures?.length && <StepFigures runId={runId} figures={s.meta.figures} />}
              {s.type === "tool" && (s.tool === "record_finding" || s.tool === "submit_plan" || s.tool === "build_report" || s.output) && (
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
              {s.type === "tool" && (
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
