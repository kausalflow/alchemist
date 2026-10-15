import { useMemo, useState } from "react";
import { api, type DatasetSummary, type Hypothesis, type Plan, type RunDetail, type Step } from "../api";
import { useApi, useSession } from "../session";
import { Chip, Collapsible, ErrorNote, day } from "./bits";
import Icon from "./icons";
import StepList, { clock, describe } from "./StepList";

const EXPIRY_DAYS = 14;
const dsName = (d: string) => d.replace(/^ds\./, "");

/** The approval gate: strike, add, edit or reorder candidate causes, then approve. Nothing reads data before this. */
export default function PlanEditor({ run, onApproved, onCancel }: { run: RunDetail; onApproved: () => void; onCancel?: () => Promise<void> }) {
  const { meta } = useSession();
  const original = run.plan as Plan;
  const [plan, setPlan] = useState<Plan>(() => structuredClone(original));
  const [dropped, setDropped] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: datasets } = useApi<DatasetSummary[]>("/datasets");
  const canApprove = !!run.permissions?.approve;
  const kept = plan.hypotheses.filter((_, i) => !dropped.has(i));
  const finalPlan: Plan = { ...plan, hypotheses: kept };
  const edited = dropped.size > 0 || JSON.stringify(plan) !== JSON.stringify(original);
  const scope = (plan.scope ?? {}) as Record<string, unknown>;
  const expires = original.submitted_at ? new Date(new Date(original.submitted_at).getTime() + EXPIRY_DAYS * 86400000) : null;

  const reads = useMemo(() => [...new Set(kept.flatMap((h) => (h.datasets ?? []).map(dsName)))], [kept]);

  const setH = (i: number, h: Partial<Hypothesis>) =>
    setPlan({ ...plan, hypotheses: plan.hypotheses.map((x, j) => (j === i ? { ...x, ...h } : x)) });
  const move = (i: number, d: number) => {
    const hs = [...plan.hypotheses];
    const [x] = hs.splice(i, 1);
    hs.splice(i + d, 0, x);
    // keep the struck-out marks with their causes
    const ids = [...dropped].map((j) => plan.hypotheses[j]);
    setPlan({ ...plan, hypotheses: hs });
    setDropped(new Set(ids.map((h) => hs.indexOf(h))));
  };
  const remove = (i: number) => {
    setPlan({ ...plan, hypotheses: plan.hypotheses.filter((_, j) => j !== i) });
    setDropped(new Set([...dropped].filter((j) => j !== i).map((j) => (j > i ? j - 1 : j))));
  };
  const toggle = (i: number) => {
    const d = new Set(dropped);
    if (d.has(i)) d.delete(i);
    else d.add(i);
    setDropped(d);
  };
  const add = () => {
    const n = Math.max(0, ...plan.hypotheses.map((h) => parseInt(h.id.replace(/\D/g, "")) || 0)) + 1;
    setPlan({ ...plan, hypotheses: [...plan.hypotheses, { id: `H${n}`, cause: "", method: "", datasets: [], source: "added by reviewer" }] });
    setEditing(true);
  };
  const undo = () => {
    setPlan(structuredClone(original));
    setDropped(new Set());
  };

  const approve = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/runs/${run.run.run_id}/plan:approve`, edited ? { plan: finalPlan } : {});
      onApproved();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  const cancel = async () => {
    if (!onCancel) return;
    setBusy(true);
    try {
      await onCancel();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="split">
      <div className="split-main stack">
        <div>
          <h1 className="title">{run.question?.text}</h1>
          <div className="chips">
            {plan.metric && <Chip>Metric: {plan.metric}</Chip>}
            {Object.entries(scope).map(([k, v]) => (
              <Chip key={k} mono>
                {k.replace(/_/g, " ")} {Array.isArray(v) ? v.join(" → ") : String(v)}
              </Chip>
            ))}
            <Chip>
              Plan v{original.version ?? 1}
              {original.submitted_at && ` · drafted ${day(original.submitted_at)}`}
            </Chip>
            {edited && <Chip tone="info">Edited: saved as a new version when approved</Chip>}
          </div>
        </div>

        <section aria-labelledby="causes" className="panel">
          <div className="panel-head">
            <h2 id="causes" className="h-panel">
              Candidate causes to test
            </h2>
            {canApprove && <span className="muted small">Untick a cause to drop it, or edit its method before approving.</span>}
            {canApprove && (
              <span className="push row-center">
                <button type="button" className="ghost small" aria-pressed={editing} onClick={() => setEditing(!editing)}>
                  {editing ? "Done editing" : "Edit causes"}
                </button>
                <button type="button" className="ghost small" onClick={add}>
                  <Icon name="plus" size={14} /> Add a cause
                </button>
              </span>
            )}
          </div>
          {editing && canApprove && (
            <div className="pad-row">
              <label className="field">
                <span className="field-label">Metric</span>
                <input value={plan.metric ?? ""} onChange={(e) => setPlan({ ...plan, metric: e.target.value })} />
              </label>
            </div>
          )}
          <ol className="hyps">
            {plan.hypotheses.map((h, i) => (
              <li key={h.id + i} className={`hyp ${dropped.has(i) ? "dropped" : ""}`}>
                <span className="hyp-check">
                  {canApprove && (
                    <input type="checkbox" checked={!dropped.has(i)} onChange={() => toggle(i)} aria-label={`Include ${h.id}`} />
                  )}
                </span>
                <span className="mono accent-text">{h.id}</span>
                <div className="hyp-body">
                  {editing && canApprove ? (
                    <>
                      <div className="row-center">
                        <label className="grow">
                          <span className="sr-only">Cause {h.id}</span>
                          <input value={h.cause} placeholder="Cause" onChange={(e) => setH(i, { cause: e.target.value })} />
                        </label>
                        <button type="button" className="icon-btn" aria-label={`Move ${h.id} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                          <Icon name="up" />
                        </button>
                        <button
                          type="button"
                          className="icon-btn"
                          aria-label={`Move ${h.id} down`}
                          disabled={i === plan.hypotheses.length - 1}
                          onClick={() => move(i, 1)}
                        >
                          <Icon name="down" />
                        </button>
                        <button type="button" className="icon-btn danger" aria-label={`Delete ${h.id}`} onClick={() => remove(i)}>
                          <Icon name="trash" />
                        </button>
                      </div>
                      <label className="field">
                        <span className="field-label">Method</span>
                        <textarea rows={2} value={h.method} onChange={(e) => setH(i, { method: e.target.value })} />
                      </label>
                    </>
                  ) : (
                    <>
                      <div className="strong">{h.cause || <span className="faint">No cause written</span>}</div>
                      <div className="muted">{h.method}</div>
                    </>
                  )}
                  <div className="chips">
                    {(h.datasets ?? []).map((d) => (
                      <span key={d} className="tag data">
                        {dsName(d)}
                        {editing && canApprove && (
                          <button
                            type="button"
                            className="tag-x"
                            aria-label={`Remove ${dsName(d)} from ${h.id}`}
                            onClick={() => setH(i, { datasets: h.datasets!.filter((x) => x !== d) })}
                          >
                            <Icon name="x" size={12} />
                          </button>
                        )}
                      </span>
                    ))}
                    {editing && canApprove && (
                      <label>
                        <span className="sr-only">Add a data source to {h.id}</span>
                        <select
                          className="compact"
                          value=""
                          onChange={(e) => e.target.value && setH(i, { datasets: [...(h.datasets ?? []), e.target.value] })}
                        >
                          <option value="">Add data source</option>
                          {datasets
                            ?.filter((d) => !(h.datasets ?? []).map(dsName).includes(d.dataset))
                            .map((d) => (
                              <option key={d.dataset} value={d.dataset}>
                                {d.dataset}
                              </option>
                            ))}
                        </select>
                      </label>
                    )}
                    {h.source && <span className="tag plan">from {h.source}</span>}
                  </div>
                </div>
              </li>
            ))}
          </ol>
          {plan.notes && <p className="muted small pad-row">{plan.notes}</p>}
        </section>

        <section aria-labelledby="decide" className="dock">
          <div className="row-baseline">
            <h2 id="decide" className="h-panel warn-text">
              Your decision
            </h2>
            {expires && <span className="small warn-text">This plan expires on {day(expires)} if nobody approves it.</span>}
          </div>
          {!canApprove && <p className="muted">Only {run.question?.asker} (who asked) or an admin can approve this plan.</p>}
          <ErrorNote error={error} />
          <div className="row-center">
            {canApprove && (
              <button type="button" className="approve lg" onClick={approve} disabled={busy || kept.length === 0}>
                <Icon name="check" /> {busy ? "Starting…" : edited ? "Approve edited plan and run" : "Approve and run"}
              </button>
            )}
            {canApprove && !editing && (
              <button type="button" className="outline lg" onClick={() => setEditing(true)} disabled={busy}>
                Revise the plan
              </button>
            )}
            {canApprove && edited && (
              <button type="button" className="ghost lg" onClick={undo} disabled={busy}>
                Undo edits
              </button>
            )}
            {run.permissions?.stop && onCancel && !confirmCancel && (
              <button type="button" className="danger lg" onClick={() => setConfirmCancel(true)} disabled={busy}>
                Cancel question
              </button>
            )}
            {confirmCancel && (
              <span className="row-center confirm" role="group" aria-label="Confirm cancel">
                <span className="small">Discard this plan? Nothing has been read from the data lake.</span>
                <button type="button" className="danger" onClick={cancel} disabled={busy}>
                  Yes, cancel
                </button>
                <button type="button" className="ghost" onClick={() => setConfirmCancel(false)}>
                  Keep it
                </button>
              </span>
            )}
          </div>
          {canApprove && (
            <p className="muted small">
              Approving lets the agent read the {reads.length} data {reads.length === 1 ? "source" : "sources"} listed
              {meta ? `, within ${meta.budgets.max_steps} tool calls and ${meta.budgets.max_output_tokens.toLocaleString()} output tokens` : ""}. You
              can pause or stop it at any step.
            </p>
          )}
        </section>
      </div>

      <aside className="split-side stack">
        <section aria-labelledby="reads" className="panel">
          <h2 id="reads" className="h-panel panel-head">
            Data it will read
          </h2>
          {reads.length === 0 && <p className="muted small pad-row">No data source is named in the plan.</p>}
          {reads.map((d) => {
            const s = datasets?.find((x) => x.dataset === d);
            return (
              <div key={d} className="ds-row">
                <div className="spread">
                  <span className="mono accent-text">{d}</span>
                  {s && <span className="faint small">card v{s.card_version}</span>}
                </div>
                {s ? (
                  <>
                    {s.description && <div className="small muted">{s.description}</div>}
                    <div className="small faint">
                      <span className="mono">{s.plant}</span>
                      {s.grain && ` · ${s.grain}`}
                    </div>
                  </>
                ) : (
                  datasets && <div className="small warn-text">Not among the approved data sources you can read.</div>
                )}
              </div>
            );
          })}
        </section>
        <DraftTrail runId={run.run.run_id} />
      </aside>
    </div>
  );
}

function DraftTrail({ runId }: { runId: string }) {
  const { data, error } = useApi<{ planning: Step[]; analysis: Step[] }>(`/runs/${runId}/steps`);
  const tools = (data?.planning ?? []).filter((s) => s.type === "tool");
  return (
    <section aria-labelledby="trail" className="panel pad stack-sm">
      <h2 id="trail" className="h-panel">
        How the agent drafted this
      </h2>
      <ErrorNote error={error} />
      {data && tools.length === 0 && <p className="muted small">No planning steps were recorded.</p>}
      <ol className="trail">
        {tools.slice(-6).map((s, i) => (
          <li key={i}>
            <span className="mono faint small">{clock(s.at)}</span>
            <span className="muted small">{describe(s)}</span>
          </li>
        ))}
      </ol>
      {data && data.planning.length > 0 && (
        <Collapsible title={<span className="small">All {tools.length} planning steps, with model notes</span>}>
          <StepList steps={data.planning} />
        </Collapsible>
      )}
    </section>
  );
}
