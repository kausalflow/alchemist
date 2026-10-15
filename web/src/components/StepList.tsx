import type { Step } from "../api";
import { Code, Collapsible } from "./bits";

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

function describe(s: Step): string {
  const inp = s.input ?? {};
  const purpose = (inp.purpose as string) || (inp.query as string) || (inp.statement as string) || (inp.summary as string) || "";
  return `${VERB[s.tool ?? ""] ?? s.tool}${purpose ? `: ${purpose}` : ""}`;
}

/** Model turns and tool calls, newest last. Code and SQL are shown in full; outputs are what the model saw. */
export default function StepList({ steps, live = false }: { steps: Step[]; live?: boolean }) {
  const shown = steps.filter((s) => s.type === "tool" || (s.text && s.text.trim()));
  if (!shown.length) return <p className="muted">{live ? "Waiting for the first step…" : "No steps recorded."}</p>;
  return (
    <ol className="steps">
      {shown.map((s, i) =>
        s.type === "model" ? (
          <li key={i} className="step model">
            <span className="muted small">model</span> {s.text}
          </li>
        ) : (
          <li key={i} className={`step ${s.is_error ? "err" : ""}`}>
            <Collapsible
              title={
                <>
                  {describe(s)}
                  {s.is_error && <span className="chip status-failed">error</span>}
                  {s.seconds !== undefined && <span className="muted small"> · {s.seconds}s</span>}
                </>
              }
            >
              {typeof s.input?.sql === "string" && <Code lang="sql">{(s.input.sql as string).trim()}</Code>}
              {typeof s.input?.code === "string" && <Code lang="python">{s.input.code as string}</Code>}
              {s.tool === "record_finding" && <Code>{JSON.stringify(s.input, null, 2)}</Code>}
              {s.tool === "submit_plan" || s.tool === "build_report" ? <Code>{JSON.stringify(s.input, null, 2)}</Code> : null}
              {s.output && (
                <>
                  <div className="muted small">Result returned to the model</div>
                  <Code>{s.output}</Code>
                </>
              )}
            </Collapsible>
          </li>
        ),
      )}
      {live && <li className="step pulse muted">working…</li>}
    </ol>
  );
}
