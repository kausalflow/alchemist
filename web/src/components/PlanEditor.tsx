import { useState } from "react";
import { api, type DatasetSummary, type Hypothesis, type Plan, type RunDetail } from "../api";
import { useApi } from "../session";
import { ErrorNote } from "./bits";

/** Plan review: strike, add, edit or reorder candidate causes, then approve. Nothing reads data before this. */
export default function PlanEditor({ run, onApproved }: { run: RunDetail; onApproved: () => void }) {
  const original = run.plan as Plan;
  const [plan, setPlan] = useState<Plan>(() => structuredClone(original));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: datasets } = useApi<DatasetSummary[]>("/datasets");
  const canApprove = run.permissions?.approve;
  const edited = JSON.stringify(plan) !== JSON.stringify(original);
  const scope = (plan.scope ?? {}) as Record<string, unknown>;

  const setH = (i: number, h: Partial<Hypothesis>) =>
    setPlan({ ...plan, hypotheses: plan.hypotheses.map((x, j) => (j === i ? { ...x, ...h } : x)) });
  const move = (i: number, d: number) => {
    const hs = [...plan.hypotheses];
    const [x] = hs.splice(i, 1);
    hs.splice(i + d, 0, x);
    setPlan({ ...plan, hypotheses: hs });
  };
  const remove = (i: number) => setPlan({ ...plan, hypotheses: plan.hypotheses.filter((_, j) => j !== i) });
  const add = () => {
    const n = Math.max(0, ...plan.hypotheses.map((h) => parseInt(h.id.replace(/\D/g, "")) || 0)) + 1;
    setPlan({ ...plan, hypotheses: [...plan.hypotheses, { id: `H${n}`, cause: "", method: "", datasets: [], source: "added by reviewer" }] });
  };

  const approve = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/runs/${run.run.run_id}/plan:approve`, edited ? { plan } : {});
      onApproved();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <section className="card plan">
      <div className="spread">
        <h2>Review the plan</h2>
        <span className="muted small">
          plan v{original.version ?? 1}
          {edited && " · edited, will be saved as a new version"}
        </span>
      </div>
      {!canApprove && <p className="muted">Only {run.question?.asker} (who asked) or an admin can approve this plan.</p>}
      <label>
        Metric
        <input value={plan.metric ?? ""} onChange={(e) => setPlan({ ...plan, metric: e.target.value })} disabled={!canApprove} />
      </label>
      <div className="scope">
        {Object.entries(scope).map(([k, v]) => (
          <div key={k}>
            <span className="muted small">{k.replace(/_/g, " ")}</span> {Array.isArray(v) ? v.join(" to ") : String(v)}
          </div>
        ))}
      </div>
      <h3>Candidate causes</h3>
      <ol className="hyps">
        {plan.hypotheses.map((h, i) => (
          <li key={h.id + i} className="hyp">
            <div className="hyp-head">
              <strong>{h.id}</strong>
              <input
                className="grow"
                value={h.cause}
                placeholder="Cause"
                onChange={(e) => setH(i, { cause: e.target.value })}
                disabled={!canApprove}
              />
              {canApprove && (
                <span className="actions">
                  <button className="icon" title="Move up" disabled={i === 0} onClick={() => move(i, -1)}>
                    ↑
                  </button>
                  <button className="icon" title="Move down" disabled={i === plan.hypotheses.length - 1} onClick={() => move(i, 1)}>
                    ↓
                  </button>
                  <button className="icon" title="Strike this cause" onClick={() => remove(i)}>
                    ✕
                  </button>
                </span>
              )}
            </div>
            <label className="small">
              Method
              <textarea rows={2} value={h.method} onChange={(e) => setH(i, { method: e.target.value })} disabled={!canApprove} />
            </label>
            <div className="dslist small">
              Data:{" "}
              {(h.datasets ?? []).map((d) => (
                <span key={d} className="chip">
                  {d}
                  {canApprove && (
                    <button className="icon" onClick={() => setH(i, { datasets: h.datasets!.filter((x) => x !== d) })}>
                      ×
                    </button>
                  )}
                </span>
              ))}
              {canApprove && (
                <select
                  value=""
                  onChange={(e) => e.target.value && setH(i, { datasets: [...(h.datasets ?? []), e.target.value] })}
                >
                  <option value="">+ dataset</option>
                  {datasets
                    ?.filter((d) => !(h.datasets ?? []).includes(d.dataset))
                    .map((d) => (
                      <option key={d.dataset} value={d.dataset}>
                        {d.dataset}
                      </option>
                    ))}
                </select>
              )}
              {h.source && <span className="muted"> · source: {h.source}</span>}
            </div>
          </li>
        ))}
      </ol>
      {canApprove && (
        <button className="link" onClick={add}>
          + Add a cause
        </button>
      )}
      {plan.notes && <p className="muted small">{plan.notes}</p>}
      <ErrorNote error={error} />
      {canApprove && (
        <div className="buttons">
          <button className="primary" onClick={approve} disabled={busy || plan.hypotheses.length === 0}>
            {busy ? "Starting…" : edited ? "Approve edited plan and run" : "Approve and run"}
          </button>
          {edited && (
            <button onClick={() => setPlan(structuredClone(original))} disabled={busy}>
              Undo edits
            </button>
          )}
        </div>
      )}
    </section>
  );
}
