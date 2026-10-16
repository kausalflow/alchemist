import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, fileUrl, type NotebookCell, type RunDetail, type RunSummary, type Step } from "../api";
import { useApi, useSession } from "../session";
import { Chip, Code, Collapsible, ErrorNote, day, fmtValue, shortId, statusLabel, when } from "./bits";
import Icon from "./icons";
import StepList from "./StepList";

// ------------------------------------------------------------------ answer

/** Replays and reruns: how the re-executed findings compared with the original. */
export function ResultCheck({ run }: { run: RunDetail }) {
  const check = run.result_check?.status;
  if (run.run.mode === "live" || !check) return null;
  return (
    <div className={`callout ${check.mismatches ? "warn" : "ok"}`}>
      <strong>
        {run.run.mode === "replay" ? "Replay on the stored snapshots" : "Rerun on current data"}: {check.compared} findings compared,{" "}
        {check.mismatches} {check.mismatches === 1 ? "mismatch" : "mismatches"}.
      </strong>
      <p className="muted small">
        {run.run.mode === "replay"
          ? "The original notebook was re-executed in a fresh kernel on the data the original run read; every finding is compared by value."
          : "The original notebook was re-executed against the data as it is now; a difference means the data changed."}
      </p>
      {!!run.manifest?.plugin_drift?.length && (
        <p className="small warn-text">Plugins changed since the original run: {(run.manifest.plugin_drift as string[]).join("; ")}</p>
      )}
    </div>
  );
}

export function FindingsTab({ run }: { run: RunDetail }) {
  const findings = run.findings ?? [];
  const [, setParams] = useSearchParams();
  const replay = run.run.mode !== "live";
  return (
    <div className="stack">
      <ResultCheck run={run} />
      <p className="muted small">
        Every number in the answer is filled in from these findings, each computed in a notebook cell. Open a finding to see its cell.
      </p>
      {findings.length === 0 ? (
        <p className="muted">No findings were recorded.</p>
      ) : (
        <div className="table-wrap">
          <table className="table findings">
            <thead>
              <tr>
                <th scope="col">Cause</th>
                <th scope="col">Finding</th>
                <th scope="col" className="num">
                  Value
                </th>
                <th scope="col">Verdict</th>
                <th scope="col">Confidence</th>
                <th scope="col">Evidence</th>
                {replay && <th scope="col">Matches original</th>}
              </tr>
            </thead>
            <tbody>
              {findings.map((f) => (
                <tr key={f.id}>
                  <td className="nowrap mono accent-text">{f.hypothesis}</td>
                  <td>{f.statement}</td>
                  <td className="num mono nowrap">{fmtValue(f.value, f.unit)}</td>
                  <td>
                    <span className={`pill verdict-${f.verdict}`}>{f.verdict}</span>
                  </td>
                  <td>{f.confidence}</td>
                  <td className="mono small">
                    <button type="button" className="link chiplink" onClick={() => setParams({ tab: "notebook", cell: f.id })}>
                      {f.id}
                    </button>
                    {(f.evidence ?? []).map((e) => (
                      <Evidence key={e} run={run} id={e} />
                    ))}
                  </td>
                  {replay && <td>{f.matches_original === undefined ? "" : f.matches_original ? "yes" : <strong className="bad-text">NO</strong>}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {run.review && <ReviewTab run={run} />}
    </div>
  );
}

function Evidence({ run, id }: { run: RunDetail; id: string }) {
  const [, setParams] = useSearchParams();
  if (/^q\d+$/.test(id))
    return (
      <button type="button" className="link chiplink" onClick={() => setParams({ tab: "data", query: id })}>
        {id}
      </button>
    );
  if (id.startsWith("figures/"))
    return (
      <a className="chiplink" href={fileUrl(run.run.run_id, id)} target="_blank" rel="noreferrer">
        {id.replace("figures/", "")}
      </a>
    );
  return (
    <button type="button" className="link chiplink" onClick={() => setParams({ tab: "plan" })}>
      {id}
    </button>
  );
}

const VERDICTS = [
  { id: "confirmed", label: "Confirm", tone: "approve" },
  { id: "partly_confirmed", label: "Partly", tone: "outline" },
  { id: "rejected", label: "Reject", tone: "danger" },
];

/** The owner's (or a workspace member's) judgement of the answer, kept with the run for later questions to find. */
export function VerdictPanel({ run, reload }: { run: RunDetail; reload: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const verdicts = run.verdicts ?? [];
  const submit = async (verdict: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/runs/${run.run.run_id}/verdict`, { verdict, note });
      setNote("");
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-labelledby="verdict" className="dock">
      <h2 id="verdict" className="h-panel warn-text">
        {verdicts.length ? "Verdicts" : "Your verdict"}
      </h2>
      {verdicts.map((v, i) => (
        <p key={i} className="small">
          <strong>{v.actor}</strong> {v.verdict.replace("_", " ")} · {when(v.at)}
          {v.note && <> · “{v.note}”</>}
        </p>
      ))}
      {run.permissions?.verdict && (
        <>
          <p className="small muted">
            Does this answer hold up against what you know of the line? Confirmed answers become past investigations that later questions can
            find.
          </p>
          <label className="field">
            <span className="field-label">Note (optional)</span>
            <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What did you check?" />
          </label>
          <div className="row-center">
            {VERDICTS.map((v) => (
              <button key={v.id} type="button" className={`${v.tone} grow-btn`} onClick={() => submit(v.id)} disabled={busy}>
                {v.label}
              </button>
            ))}
          </div>
        </>
      )}
      <ErrorNote error={error} />
    </section>
  );
}

// ------------------------------------------------------------------ plan (as approved)

/** Methods and plan: the plan as approved, plus the plant knowledge the agent read. */
export function MethodsTab({ run }: { run: RunDetail }) {
  const hasKnowledge = !!run.knowledge?.citations.length || !!run.knowledge?.structured.length;
  return (
    <div className="stack">
      {run.plan ? <PlanTab run={run} /> : <p className="muted">No plan was recorded.</p>}
      {hasKnowledge && <KnowledgeTab run={run} />}
    </div>
  );
}

export function PlanTab({ run }: { run: RunDetail }) {
  const p = run.plan!;
  return (
    <div>
      <h3>Plan</h3>
      <p className="muted small">
        Plan v{p.version ?? 1}
        {p.approved_by && (
          <>
            {" "}
            · approved by {p.approved_by} {when(p.approved_at)}
          </>
        )}
      </p>
      {p.metric && (
        <p>
          <strong>Metric:</strong> {p.metric}
        </p>
      )}
      {p.scope && <Code>{JSON.stringify(p.scope, null, 2)}</Code>}
      <table className="table">
        <thead>
          <tr>
            <th>ID</th>
            <th>Cause</th>
            <th>Method</th>
            <th>Data</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {p.hypotheses.map((h) => (
            <tr key={h.id}>
              <td>{h.id}</td>
              <td>{h.cause}</td>
              <td>{h.method}</td>
              <td>{h.datasets?.join(", ")}</td>
              <td className="small">{h.source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ data used

export function DataTab({ run }: { run: RunDetail }) {
  const [params] = useSearchParams();
  const focus = params.get("query");
  return (
    <div>
      <h3>Datasets pinned at approval</h3>
      <p className="muted small">
        The run could query only these views, built from the catalog card version shown. Columns marked hidden never reached the model;
        pseudonymised ones were replaced by keyed hashes.
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Dataset</th>
            <th>Card</th>
            <th>Plant</th>
            <th>Columns</th>
          </tr>
        </thead>
        <tbody>
          {(run.datasets ?? []).map((d) => (
            <tr key={d.dataset}>
              <td>
                <Link to={`/catalog/${d.dataset}`}>{d.dataset}</Link>
              </td>
              <td>v{d.card_version}</td>
              <td>{d.plant}</td>
              <td className="small">
                {d.columns.map((c) => (
                  <span key={c.name} className={`col ${c.exposure}`} title={c.exposure}>
                    {c.name}
                    {c.exposure === "never_to_model" ? " (hidden)" : c.exposure === "pseudonymise" ? " (pseudonymised)" : ""}
                  </span>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Queries</h3>
      <p className="muted small">
        Each query's result was saved in the run folder by content hash, with the source objects it read, so a replay uses exactly the
        same rows even though the lake has no versioning.
      </p>
      {(run.queries ?? []).map((q) => (
        <Collapsible
          key={q.id}
          open={q.id === focus}
          title={
            <>
              <strong>{q.id}</strong> {q.purpose} <span className="muted small">· {q.rows.toLocaleString()} rows · {q.datasets.join(", ")}</span>
            </>
          }
        >
          <Code lang="sql">{q.sql.trim()}</Code>
          <div className="small">
            Snapshot <a href={fileUrl(run.run.run_id, q.snapshot, true)}>{q.snapshot}</a> · card versions{" "}
            {Object.entries(q.card_versions)
              .map(([k, v]) => `${k}@${v}`)
              .join(", ")}
          </div>
          <Collapsible title={`Source objects (${q.objects.length})`}>
            <table className="table small">
              <thead>
                <tr>
                  <th>Object</th>
                  <th>ETag</th>
                  <th>Size</th>
                  <th>Modified</th>
                </tr>
              </thead>
              <tbody>
                {q.objects.map((o) => (
                  <tr key={o.key}>
                    <td className="break">{o.key}</td>
                    <td>
                      <code>{o.etag.slice(0, 12)}</code>
                    </td>
                    <td>{o.size.toLocaleString()}</td>
                    <td>{o.last_modified}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Collapsible>
          <SnapshotPreview runId={run.run.run_id} queryId={q.id} />
        </Collapsible>
      ))}
    </div>
  );
}

function SnapshotPreview({ runId, queryId }: { runId: string; queryId: string }) {
  const [show, setShow] = useState(false);
  const { data, error } = useApi<{ columns: { name: string; type: string }[]; rows: number; head: unknown[][] }>(
    show ? `/runs/${runId}/snapshots/${queryId}?limit=25` : null,
  );
  if (!show)
    return (
      <button className="link small" onClick={() => setShow(true)}>
        Preview the first rows
      </button>
    );
  return (
    <div className="scrollx">
      <ErrorNote error={error} />
      {data && (
        <table className="table small">
          <thead>
            <tr>
              {data.columns.map((c) => (
                <th key={c.name} title={c.type}>
                  {c.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.head.map((row, i) => (
              <tr key={i}>
                {row.map((v, j) => (
                  <td key={j} className="nowrap">
                    {v === null ? <span className="muted">null</span> : String(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ notebook

export function NotebookTab({ run }: { run: RunDetail }) {
  const { data, error } = useApi<{ cells: NotebookCell[] }>(`/runs/${run.run.run_id}/notebook`);
  const [params] = useSearchParams();
  const focus = params.get("cell");
  useEffect(() => {
    if (data && focus) document.getElementById(`cell-${focus}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [data, focus]);
  return (
    <div>
      <p className="muted small">
        Every query, computation and finding the agent ran, in order, with its outputs. Download{" "}
        <a href={fileUrl(run.run.run_id, "notebook.ipynb", true)}>notebook.ipynb</a> to open it in Jupyter; set{" "}
        <code>run_mode = 'replay'</code> in its first cell to re-run it on the stored snapshots.
      </p>
      <ErrorNote error={error} />
      {data?.cells.map((c, i) => {
        const anchor = c.meta.finding_id ?? c.meta.query_id ?? c.meta.cell_id ?? `md${i}`;
        const hl = focus && (focus === c.meta.finding_id || focus === c.meta.cell_id || focus === c.meta.query_id);
        if (c.type === "markdown")
          return (
            <div key={i} className="nbmd">
              {c.source}
            </div>
          );
        return (
          <div key={i} id={`cell-${anchor}`} className={`nbcell ${hl ? "highlight" : ""}`}>
            <div className="nbmeta small">
              <code>{c.meta.cell_id}</code> <Chip>{c.meta.kind ?? "code"}</Chip> {c.meta.query_id} {c.meta.finding_id} {c.meta.purpose}
            </div>
            <Code lang="python">{c.source}</Code>
            {c.outputs.map((o, j) =>
              o.type === "image" ? (
                <img key={j} src={`data:image/png;base64,${o.png}`} alt={`output of ${c.meta.cell_id}`} />
              ) : (
                <pre key={j} className={`nbout ${o.type === "error" ? "err" : ""}`}>
                  {o.text}
                </pre>
              ),
            )}
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ knowledge, review, steps, report

export function KnowledgeTab({ run }: { run: RunDetail }) {
  const k = run.knowledge!;
  return (
    <div>
      <h3>Document passages the agent read</h3>
      <table className="table">
        <thead>
          <tr>
            <th>Document</th>
            <th>Version</th>
            <th>Title</th>
            <th>Section</th>
          </tr>
        </thead>
        <tbody>
          {k.citations.map((c) => (
            <tr key={`${c.doc}-${c.section}`}>
              <td>{c.doc}</td>
              <td>
                v{c.version} {c.stale && <Chip tone="warn">past review date</Chip>}
              </td>
              <td>{c.title}</td>
              <td>{c.section}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {k.structured.length > 0 && (
        <>
          <h3>Structured knowledge copied into the run</h3>
          <p className="muted small">Spec limits, residence times and similar facts used directly by code, so replays use the same values.</p>
          <ul>
            {k.structured.map((s) => (
              <li key={s.name}>
                <a href={fileUrl(run.run.run_id, `inputs/knowledge/${s.name}.json`)} target="_blank" rel="noreferrer">
                  {s.name}
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

export function ReviewTab({ run }: { run: RunDetail }) {
  const r = run.review!;
  return (
    <div>
      <p>
        An independent reviewer ({r.model}) read the plan, every notebook cell and the findings before the report was written.{" "}
        <strong>{r.ok ? "It found no blocking problem." : "It raised blocking problems."}</strong>
      </p>
      {r.issues.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Finding</th>
              <th>Problem</th>
              <th>Suggestion</th>
            </tr>
          </thead>
          <tbody>
            {r.issues.map((i, n) => (
              <tr key={n}>
                <td>{i.finding}</td>
                <td>{i.problem}</td>
                <td>{i.suggestion}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted">{r.notes}</p>
    </div>
  );
}

export function StepsTab({ run }: { run: RunDetail }) {
  const { data, error } = useApi<{ planning: Step[]; analysis: Step[] }>(`/runs/${run.run.run_id}/steps`, [run.run.status]);
  return (
    <div>
      <p className="muted small">
        What the model said and every tool it called, with the exact input and the output it got back. Kept for audit; a replay never
        asks the model again.
      </p>
      <ErrorNote error={error} />
      {data && (
        <>
          <h3>Planning</h3>
          <StepList steps={data.planning} />
          {data.analysis.length > 0 && (
            <>
              <h3>Analysis</h3>
              <StepList steps={data.analysis} runId={run.run.run_id} />
            </>
          )}
        </>
      )}
    </div>
  );
}

export function ReportTab({ run }: { run: RunDetail }) {
  const url = fileUrl(run.run.run_id, "report/report.html");
  return (
    <div>
      <p className="small">
        <a href={url} target="_blank" rel="noreferrer">
          Open in a new tab
        </a>{" "}
        · <a href={fileUrl(run.run.run_id, "report/report.html", true)}>download</a>
      </p>
      <iframe className="report" src={url} title="Report" sandbox="allow-same-origin" />
    </div>
  );
}

// ------------------------------------------------------------------ provenance

type SealCheck = { ok: boolean; files?: number; problems: string[]; seal_sha256?: string };

/** Start a replay (same snapshots) or rerun (current data) of a sealed run, then open it. */
export function useReplay(run: RunDetail, reload: () => void) {
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const start = async (mode: "replay" | "rerun") => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<RunSummary>(`/runs/${run.run.run_id}:replay`, { mode });
      nav(`/runs/${r.run_id}`);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { start, busy, error };
}

function useVerify(runId: string) {
  const [check, setCheck] = useState<SealCheck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const verify = () =>
    api
      .get<SealCheck>(`/runs/${runId}/verify`)
      .then(setCheck)
      .catch((e) => setError((e as Error).message));
  return { check, error, verify };
}

function SealResult({ check }: { check: SealCheck | null }) {
  if (!check) return null;
  return (
    <p className={`small ${check.ok ? "ok-text" : "error"}`} role="status">
      {check.ok
        ? `Seal verified: ${check.files} files match the SHA-256 list in SEALED.`
        : `Verification failed: ${check.problems.slice(0, 5).join("; ")}`}
    </p>
  );
}

/** Side panel on a sealed run: what pins it down, and whether a replay has matched. */
export function ReproducePanel({ run }: { run: RunDetail }) {
  const [, setParams] = useSearchParams();
  const { check, error, verify } = useVerify(run.run.run_id);
  const m = run.manifest ?? {};
  const p = run.plan;
  const replays = (run.children ?? []).filter((c) => c.mode === "replay" && ["completed", "mismatch"].includes(c.status));
  const lastReplay = replays[replays.length - 1];
  const rc = run.run.mode !== "live" ? run.result_check?.status : undefined;
  return (
    <section aria-labelledby="prov" className="panel pad stack-sm">
      <h2 id="prov" className="h-panel">
        How to reproduce this
      </h2>
      <dl className="facts">
        {p && (
          <>
            <dt>Plan</dt>
            <dd>
              v{p.version ?? 1}
              {p.approved_by && `, approved by ${p.approved_by}`}
              {p.approved_at && `, ${when(p.approved_at)}`}
            </dd>
          </>
        )}
        <dt>Data</dt>
        <dd className="mono small">
          {(run.queries ?? []).length} {(run.queries ?? []).length === 1 ? "snapshot" : "snapshots"}, pinned by ETag
        </dd>
        <dt>Code</dt>
        <dd className="mono small">{run.has_notebook ? "notebook.ipynb" : "no notebook"}</dd>
        {m.model && (
          <>
            <dt>Models</dt>
            <dd className="mono small">
              {m.model.lead}
              {m.model.reviewer && ` + reviewer ${m.model.reviewer}`}
            </dd>
          </>
        )}
        <dt>Seal</dt>
        <dd className="mono small">
          {run.run.sealed ? (check?.seal_sha256 ? `sha256 ${check.seal_sha256.slice(0, 8)}…${check.seal_sha256.slice(-4)}` : "sealed") : "not sealed"}
        </dd>
      </dl>
      {rc && (
        <div className={`callout small ${rc.mismatches ? "warn" : "ok"}`}>
          This {run.run.mode}: {rc.compared} findings compared, {rc.mismatches} {rc.mismatches === 1 ? "mismatch" : "mismatches"}
        </div>
      )}
      {lastReplay && (
        <div className={`callout small ${lastReplay.status === "mismatch" ? "warn" : "ok"}`}>
          {lastReplay.status === "mismatch" ? <Icon name="alert" size={14} /> : <Icon name="check" size={14} />}
          Replayed {day(lastReplay.created_at)}: {lastReplay.status === "mismatch" ? "values did not match" : "all values matched"}
        </div>
      )}
      <div className="row-center">
        {run.run.sealed && (
          <button type="button" className="ghost small" onClick={verify}>
            Verify seal
          </button>
        )}
        {run.manifest && (
          <button type="button" className="link small" onClick={() => setParams({ tab: "provenance" })}>
            Full provenance manifest
          </button>
        )}
      </div>
      <SealResult check={check} />
      <ErrorNote error={error} />
    </section>
  );
}

/** How a replay or rerun came out, in words: matched, did not match, still running, failed. */
export function replayOutcome(r: RunSummary): [string, string] {
  if (r.status === "completed") return ["matched", "ok-text"];
  if (r.status === "mismatch") return ["did not match", "bad-text"];
  if (r.status === "running" || r.status === "paused") return [statusLabel(r.status).toLowerCase(), "info-text"];
  return [statusLabel(r.status).toLowerCase(), "muted"];
}

/** Side panel: the original run of this question and its replays and reruns, with who ran each and whether it matched. */
export function QuestionRuns({ run }: { run: RunDetail }) {
  const { me } = useSession();
  const qid = run.run.question_id;
  const { data } = useApi<RunSummary[]>(qid ? `/runs?question_id=${encodeURIComponent(qid)}` : null);
  // Oldest first; the original leads when times tie.
  const runs = (data ?? (run.children ? [run.run, ...run.children] : [run.run]))
    .slice()
    .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? "") || Number(a.mode !== "live") - Number(b.mode !== "live") || a.run_id.localeCompare(b.run_id));
  const reproductions = runs.filter((r) => r.mode !== "live").length;
  return (
    <section aria-labelledby="qruns" className="panel pad stack-sm">
      <h2 id="qruns" className="h-panel">
        Runs of this question
      </h2>
      <div className="small muted">
        {reproductions > 0 ? `The original and ${reproductions} ${reproductions === 1 ? "replay" : "replays"}, oldest first. ` : ""}
        Project <span className="mono">{run.run.project}</span> · workspace {run.run.workspace} · visible to its members who can read{" "}
        <span className="mono">{run.run.plants?.join(", ") || "its plants"}</span>
      </div>
      <ol className="trail">
        {runs.map((r) => {
          const [outcome, tone] = r.mode === "live" ? [statusLabel(r.status).toLowerCase(), ""] : replayOutcome(r);
          const label = `run ${shortId(r.run_id)}`;
          return (
            <li key={r.run_id} className={r.run_id === run.run.run_id ? "current" : ""}>
              <span className={`small ${r.mode === "live" ? "accent-text" : "muted"}`}>{r.mode === "live" ? "Original" : r.mode === "replay" ? "Replay" : "Rerun"}</span>
              <span className="small stack-xs">
                <span>
                  {r.run_id === run.run.run_id ? <strong>{label} (this run)</strong> : <Link to={`/runs/${r.run_id}`}>{label}</Link>}
                  {" · "}
                  <span className={tone}>{outcome}</span>
                </span>
                <span className="faint">
                  {when(r.created_at)} · by {me && r.user_id === me.id ? "you" : r.user_id}
                </span>
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function ProvenanceTab({ run, reload }: { run: RunDetail; reload: () => void }) {
  const m = run.manifest!;
  const { start, busy, error } = useReplay(run, reload);
  const { check, error: verr, verify } = useVerify(run.run.run_id);
  const id = run.run.run_id;

  return (
    <div className="stack">
      <section className="panel pad">
        <h3>Reproduce this run</h3>
        <p className="muted small">
          A replay re-executes the notebook on the stored snapshots and compares every finding; a rerun does the same on current data to
          show whether the answer still holds.
        </p>
        {run.permissions?.replay && (
          <div className="row-center">
            <button type="button" onClick={() => start("replay")} disabled={busy}>
              Replay on snapshots
            </button>
            <button type="button" onClick={() => start("rerun")} disabled={busy}>
              Rerun on current data
            </button>
            <button type="button" onClick={verify}>
              Verify seal
            </button>
            <a className="button" href={`/api/runs/${id}/bundle`}>
              Download run folder (zip)
            </a>
          </div>
        )}
        <ErrorNote error={error ?? verr} />
        <SealResult check={check} />
        {(run.children ?? []).length > 0 && (
          <>
            <h4>Replays and reruns</h4>
            <ul className="plain">
              {run.children!.map((c) => (
                <li key={c.run_id}>
                  <Link to={`/runs/${c.run_id}`}>
                    {c.mode} {shortId(c.run_id)}
                  </Link>{" "}
                  <span className={`small ${replayOutcome(c)[1]}`}>{replayOutcome(c)[0]}</span>{" "}
                  <span className="muted small">by {c.user_id} · {when(c.created_at)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
      <div className="table-wrap">
        <table className="table kv">
          <tbody>
            <tr>
              <th scope="row">Lead model</th>
              <td className="mono">{m.model?.lead}</td>
            </tr>
            <tr>
              <th scope="row">Reviewer model</th>
              <td className="mono">{m.model?.reviewer}</td>
            </tr>
            <tr>
              <th scope="row">Prompts and tools</th>
              <td>
                <code>{m.model?.prompts}</code>
              </td>
            </tr>
            <tr>
              <th scope="row">Python and packages</th>
              <td className="small mono">
                Python {m.environment?.python} ·{" "}
                {Object.entries(m.environment?.packages ?? {})
                  .map(([k, v]) => `${k} ${v ?? "-"}`)
                  .join(", ")}
              </td>
            </tr>
            <tr>
              <th scope="row">Lockfile</th>
              <td>
                <code>{m.environment?.uv_lock_sha256?.slice(0, 16)}</code>
              </td>
            </tr>
            <tr>
              <th scope="row">Access</th>
              <td>
                {m.access?.user} · <span className="mono">{m.access?.workspace}/{m.access?.project}</span> · plants{" "}
                <span className="mono">{m.access?.plants?.join(", ")}</span>
              </td>
            </tr>
            <tr>
              <th scope="row">Budget used</th>
              <td className="mono small">{JSON.stringify(m.usage)}</td>
            </tr>
            <tr>
              <th scope="row">Ended</th>
              <td>{m.analysis_ended ?? m.planning_ended ?? m.status}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <Collapsible title="manifest.json">
        <Code>{JSON.stringify(m, null, 2)}</Code>
      </Collapsible>
      <p className="small">
        Files: <a href={fileUrl(id, "manifest.json")}>manifest.json</a> · <a href={fileUrl(id, "findings.json")}>findings.json</a> ·{" "}
        <a href={fileUrl(id, "SEALED")}>SEALED</a> · <a href={fileUrl(id, "logs/transcript.jsonl", true)}>transcript.jsonl</a>
      </p>
    </div>
  );
}
