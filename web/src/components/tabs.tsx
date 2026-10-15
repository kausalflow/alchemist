import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, fileUrl, type NotebookCell, type RunDetail, type RunSummary, type Step } from "../api";
import { useApi } from "../session";
import { Chip, Code, Collapsible, ErrorNote, StatusChip, fmtValue, when } from "./bits";
import StepList from "./StepList";

// ------------------------------------------------------------------ answer

export function AnswerTab({ run, reload }: { run: RunDetail; reload: () => void }) {
  const findings = run.findings ?? [];
  const [, setParams] = useSearchParams();
  const check = run.result_check?.status;
  return (
    <div>
      {run.run.mode !== "live" && check && (
        <div className={`card ${check.mismatches ? "warn" : "okbox"}`}>
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
            <p className="small warn">
              Plugins changed since the original run: {(run.manifest.plugin_drift as string[]).join("; ")}
            </p>
          )}
        </div>
      )}
      {run.conclusion_html ? (
        <article className="card conclusion" dangerouslySetInnerHTML={{ __html: run.conclusion_html }} />
      ) : (
        run.run.mode === "live" && <p className="muted">No conclusion was written for this run.</p>
      )}
      <h3>Findings</h3>
      <p className="muted small">
        Every number in the conclusion is filled in from these findings, each computed in a notebook cell. Click a finding to see its
        cell.
      </p>
      <table className="table findings">
        <thead>
          <tr>
            <th>ID</th>
            <th>Cause</th>
            <th>Finding</th>
            <th>Value</th>
            <th>Verdict</th>
            <th>Confidence</th>
            <th>Evidence</th>
            {run.run.mode !== "live" && <th>Matches original</th>}
          </tr>
        </thead>
        <tbody>
          {findings.map((f) => (
            <tr key={f.id}>
              <td>
                <button className="link" onClick={() => setParams({ tab: "notebook", cell: f.id })}>
                  {f.id}
                </button>
              </td>
              <td>{f.hypothesis}</td>
              <td>{f.statement}</td>
              <td className="nowrap">
                <strong>{fmtValue(f.value, f.unit)}</strong>
              </td>
              <td>
                <span className={`verdict ${f.verdict}`}>{f.verdict}</span>
              </td>
              <td>{f.confidence}</td>
              <td className="small">
                {(f.evidence ?? []).map((e) => (
                  <Evidence key={e} run={run} id={e} />
                ))}
              </td>
              {run.run.mode !== "live" && <td>{f.matches_original === undefined ? "" : f.matches_original ? "yes" : "NO"}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      {run.run.mode === "live" && run.permissions?.verdict && <VerdictForm run={run} reload={reload} />}
    </div>
  );
}

function Evidence({ run, id }: { run: RunDetail; id: string }) {
  const [, setParams] = useSearchParams();
  if (/^q\d+$/.test(id))
    return (
      <button className="link chiplink" onClick={() => setParams({ tab: "data", query: id })}>
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
    <button className="link chiplink" onClick={() => setParams({ tab: "knowledge" })}>
      {id}
    </button>
  );
}

function VerdictForm({ run, reload }: { run: RunDetail; reload: () => void }) {
  const [verdict, setVerdict] = useState("confirmed");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
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
    <section className="card">
      <h3>Your verdict</h3>
      <p className="muted small">Confirmed answers become past investigations that later questions can find.</p>
      {(run.verdicts ?? []).map((v, i) => (
        <p key={i} className="small">
          <strong>{v.actor}</strong> {v.verdict.replace("_", " ")} · {when(v.at)}
          {v.note && <> · “{v.note}”</>}
        </p>
      ))}
      <div className="row">
        <select value={verdict} onChange={(e) => setVerdict(e.target.value)}>
          <option value="confirmed">Confirmed</option>
          <option value="partly_confirmed">Partly confirmed</option>
          <option value="rejected">Rejected</option>
        </select>
        <input className="grow" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What did you check? (optional)" />
        <button onClick={submit} disabled={busy}>
          Record verdict
        </button>
      </div>
      <ErrorNote error={error} />
    </section>
  );
}

// ------------------------------------------------------------------ plan (as approved)

export function PlanTab({ run }: { run: RunDetail }) {
  const p = run.plan!;
  return (
    <div>
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
              <StepList steps={data.analysis} />
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

export function ProvenanceTab({ run, reload }: { run: RunDetail; reload: () => void }) {
  const m = run.manifest!;
  const nav = useNavigate();
  const [check, setCheck] = useState<{ ok: boolean; files?: number; problems: string[]; seal_sha256?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const id = run.run.run_id;

  const doReplay = async (mode: "replay" | "rerun") => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<RunSummary>(`/runs/${id}:replay`, { mode });
      nav(`/runs/${r.run_id}`);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <section className="card">
        <h3>Reproduce this run</h3>
        <p className="muted small">
          A replay re-executes the notebook on the stored snapshots and compares every finding; a rerun does the same on current data to
          show whether the answer still holds.
        </p>
        {run.permissions?.replay && (
          <div className="buttons">
            <button onClick={() => doReplay("replay")} disabled={busy}>
              Replay on snapshots
            </button>
            <button onClick={() => doReplay("rerun")} disabled={busy}>
              Rerun on current data
            </button>
            <button onClick={() => api.get<typeof check>(`/runs/${id}/verify`).then(setCheck)}>Verify seal</button>
            <a className="button" href={`/api/runs/${id}/bundle`}>
              Download run folder (zip)
            </a>
          </div>
        )}
        <ErrorNote error={error} />
        {check && (
          <p className={check.ok ? "ok" : "error"}>
            {check.ok
              ? `Seal verified: ${check.files} files match the SHA-256 list in SEALED.`
              : `Verification failed: ${check.problems.slice(0, 5).join("; ")}`}
          </p>
        )}
        {(run.children ?? []).length > 0 && (
          <>
            <h4>Replays and reruns</h4>
            <ul className="plain">
              {run.children!.map((c) => (
                <li key={c.run_id}>
                  <Link to={`/runs/${c.run_id}`}>
                    {c.mode} {c.run_id.slice(-8)}
                  </Link>{" "}
                  <StatusChip status={c.status} /> <span className="muted small">by {c.user_id} · {when(c.created_at)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>
      <table className="table kv">
        <tbody>
          <tr>
            <th>Lead model</th>
            <td>{m.model?.lead}</td>
          </tr>
          <tr>
            <th>Reviewer model</th>
            <td>{m.model?.reviewer}</td>
          </tr>
          <tr>
            <th>Prompts and tools</th>
            <td>
              <code>{m.model?.prompts}</code>
            </td>
          </tr>
          <tr>
            <th>Python and packages</th>
            <td className="small">
              Python {m.environment?.python} ·{" "}
              {Object.entries(m.environment?.packages ?? {})
                .map(([k, v]) => `${k} ${v ?? "-"}`)
                .join(", ")}
            </td>
          </tr>
          <tr>
            <th>Lockfile</th>
            <td>
              <code>{m.environment?.uv_lock_sha256?.slice(0, 16)}</code>
            </td>
          </tr>
          <tr>
            <th>Access</th>
            <td>
              {m.access?.user} · {m.access?.workspace}/{m.access?.project} · plants {m.access?.plants?.join(", ")}
            </td>
          </tr>
          <tr>
            <th>Budget used</th>
            <td>{JSON.stringify(m.usage)}</td>
          </tr>
          <tr>
            <th>Ended</th>
            <td>{m.analysis_ended ?? m.planning_ended ?? m.status}</td>
          </tr>
        </tbody>
      </table>
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
