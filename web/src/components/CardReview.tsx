import { useState } from "react";
import { api, type Card, type CardColumn, type DatasetDetail } from "../api";
import { useApi } from "../session";
import { Code, Collapsible, ErrorNote, Loading, when } from "./bits";

const EXPOSURE: Record<string, { label: string; tone: string }> = {
  model: { label: "Yes", tone: "info" },
  pseudonymise: { label: "Only as a code", tone: "warn" },
  never_to_model: { label: "Never", tone: "bad" },
};
export const exposureLabel = (e?: string) => EXPOSURE[e ?? "model"]?.label ?? e ?? "Yes";

export function ExposurePill({ exposure }: { exposure?: string }) {
  const x = EXPOSURE[exposure ?? "model"] ?? { label: exposure, tone: "" };
  return <span className={`pill ${x.tone}`}>{x.label}</span>;
}

/** What changed in a column since the approved card, in plain words ("" when nothing did). */
function change(col: CardColumn, base: Card | undefined): string {
  if (!base) return "";
  const b = (base.columns ?? []).find((c) => c.name === col.name);
  if (!b) return "New column";
  const out: string[] = [];
  if ((b.exposure ?? "model") !== (col.exposure ?? "model")) out.push(`Was: ${exposureLabel(b.exposure)}.`);
  if ((b.unit ?? "") !== (col.unit ?? "")) out.push(`Unit was ${b.unit || "not set"}.`);
  if ((b.description ?? "") !== (col.description ?? "")) out.push(b.description ? `Meaning was: ${b.description}` : "Meaning added.");
  return out.join(" ");
}

/**
 * A dataset's card as agents are told it, for review: the newest draft (changes against the approved version
 * highlighted) or the approved card, with approve / edit / reject / redraft for catalog admins.
 */
export default function CardReview({ datasetId, onChange }: { datasetId: string; onChange?: () => void }) {
  const { data, error, reload } = useApi<DatasetDetail>(`/datasets/${datasetId}`);
  const [edit, setEdit] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [actError, setActError] = useState<string | null>(null);

  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;

  const ds = data.dataset;
  const versions = (data.versions ?? []).slice().sort((a, b) => a.version - b.version);
  const latest = versions[versions.length - 1];
  const draft = latest?.status === "draft" ? latest : null;
  const card: Card | undefined = draft?.card ?? data.approved?.card ?? latest?.card;
  const base = draft && data.approved && data.approved.version !== draft.version ? data.approved.card : undefined;
  const removed = base ? (base.columns ?? []).filter((b) => !(card?.columns ?? []).some((c) => c.name === b.name)) : [];
  const checks = draft?.checks ?? latest?.checks ?? [];

  const done = (text: string) => {
    setMsg(text);
    setEdit(null);
    reload();
    onChange?.();
  };
  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    setActError(null);
    setMsg(null);
    try {
      done(await fn());
    } catch (e) {
      setActError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const approve = (edited?: Card) =>
    run(async () => {
      await api.post(`/datasets/${datasetId}/cards/${draft!.version}:approve`, { card: edited ?? null });
      return `Approved v${draft!.version}; agents use it from now on.`;
    });
  const reject = () =>
    run(async () => {
      await api.post(`/datasets/${datasetId}/cards/${draft!.version}:reject`, {});
      return `Rejected v${draft!.version}.`;
    });
  const redraft = () =>
    run(async () => {
      const r = await api.post<{ status: string; version: number }>(`/datasets/${datasetId}:index`, { force: true });
      return `Redrafted: ${r.status} v${r.version}.`;
    });
  const saveEdit = () => {
    try {
      approve(JSON.parse(edit!));
    } catch {
      setActError("The edited card is not valid JSON.");
    }
  };

  const status = draft
    ? { text: data.approved ? "New version waiting for you" : "Draft waiting for you", tone: "warn" }
    : data.approved
      ? { text: `Approved v${data.approved.version}`, tone: "ok" }
      : latest
        ? { text: `v${latest.version} ${latest.status}`, tone: "bad" }
        : { text: "No description yet", tone: "bad" };

  return (
    <div className="stack">
      <div>
        <div className="row-center">
          <h2 className="h-section mono">{ds.id}</h2>
          <span className={`pill ${status.tone}`}>{status.text}</span>
        </div>
        <div className="mono faint small break">
          {ds.s3_prefix}
          {ds.file_glob ? `/${ds.file_glob}` : ""} · metadata: {ds.metadata_uri || "no metadata file"}
        </div>
        <p className="muted small measure">
          This is what agents are told about the data. Alchemist drafted it from the metadata file and a sample of rows.
          {base && " Changes since the version agents use now are highlighted."}
          {data.approved && ` Approved v${data.approved.version} by ${data.approved.reviewed_by} ${when(data.approved.reviewed_at)}.`}
        </p>
      </div>

      {!card && <div className="callout warn">No card has been drafted yet. Redraft it from the metadata file to start.</div>}

      {card && edit === null && (
        <>
          {card.description && <p>{card.description}</p>}
          <dl className="facts wide">
            <dt>One row is</dt>
            <dd>{card.grain || <span className="warn-text">not known: please fill in</span>}</dd>
            <dt>Time</dt>
            <dd>
              {card.time_zone ? <span className="mono">{card.time_zone}</span> : <span className="warn-text">time zone unknown: please fill in</span>}
              {card.time_meaning && <span className="muted"> · {card.time_meaning}</span>}
            </dd>
            <dt>Joins with</dt>
            <dd className="mono">{card.join_keys?.length ? card.join_keys.join(", ") : <span className="warn-text sans">no join keys</span>}</dd>
            {card.refresh_lag && (
              <>
                <dt>Refresh lag</dt>
                <dd>{card.refresh_lag}</dd>
              </>
            )}
            {!!card.quirks?.length && (
              <>
                <dt>Known quirks</dt>
                <dd>
                  <ul className="bullets">
                    {card.quirks.map((q) => (
                      <li key={q}>{q}</li>
                    ))}
                  </ul>
                </dd>
              </>
            )}
          </dl>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Column</th>
                  <th scope="col">Meaning and unit</th>
                  <th scope="col">Valid range</th>
                  <th scope="col">Can the AI model see values?</th>
                </tr>
              </thead>
              <tbody>
                {(card.columns ?? []).map((c) => {
                  const ch = change(c, base);
                  return (
                    <tr key={c.name} className={ch ? "changed" : ""}>
                      <td className="mono nowrap">
                        {c.name}
                        {c.type && <div className="faint tiny">{c.type}</div>}
                      </td>
                      <td>
                        {c.description || <span className="faint">No description</span>}
                        {c.unit && <span className="muted">, {c.unit}</span>}
                        {ch && <div className="small accent-text">{ch}</div>}
                      </td>
                      <td className="mono small nowrap">{c.valid_min !== undefined && c.valid_min !== null ? `${c.valid_min} to ${c.valid_max}` : ""}</td>
                      <td>
                        <ExposurePill exposure={c.exposure} />
                      </td>
                    </tr>
                  );
                })}
                {removed.map((c) => (
                  <tr key={`rm-${c.name}`} className="changed">
                    <td className="mono">
                      <s>{c.name}</s>
                    </td>
                    <td className="small accent-text" colSpan={3}>
                      Removed since the approved version
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!!card.missing?.length && (
            <div className="callout warn small">
              <strong>Missing facts:</strong> {card.missing.join("; ")}
            </div>
          )}
          {checks.length > 0 && <div className="callout small">Checked against a sample of rows: {checks.join("; ")}</div>}
        </>
      )}

      {edit !== null && (
        <label className="field">
          <span className="field-label">Card JSON for v{draft?.version}</span>
          <textarea className="code" rows={22} value={edit} onChange={(e) => setEdit(e.target.value)} spellCheck={false} />
        </label>
      )}

      <ErrorNote error={actError} />
      {msg && (
        <p className="ok-text small" role="status">
          {msg}
        </p>
      )}

      <div className="row-center">
          {draft && edit === null && (
            <>
              <button type="button" className="approve" onClick={() => approve()} disabled={busy}>
                Approve for agents
              </button>
              <button type="button" className="outline" onClick={() => setEdit(JSON.stringify(draft.card, null, 2))} disabled={busy}>
                Edit
              </button>
              <button type="button" className="danger" onClick={reject} disabled={busy}>
                Reject
              </button>
            </>
          )}
          {draft && edit !== null && (
            <>
              <button type="button" className="approve" onClick={saveEdit} disabled={busy}>
                Approve edited card
              </button>
              <button type="button" className="ghost" onClick={() => setEdit(null)} disabled={busy}>
                Cancel editing
              </button>
            </>
          )}
          {data.versions && edit === null && (
            <button type="button" className="ghost" onClick={redraft} disabled={busy}>
              Redraft from the metadata file
            </button>
          )}
      </div>

      {versions.length > 0 && (
        <Collapsible title={`All card versions (${versions.length})`}>
          {versions
            .slice()
            .reverse()
            .map((v) => (
              <Collapsible
                key={v.version}
                title={
                  <>
                    v{v.version} <span className={`pill ${v.status === "approved" ? "ok" : v.status === "draft" ? "warn" : ""}`}>{v.status}</span>{" "}
                    <span className="muted small">
                      drafted by {v.drafted_by}
                      {v.reviewed_by && ` · reviewed by ${v.reviewed_by}`}
                    </span>
                  </>
                }
              >
                {v.checks.length > 0 && <p className="small warn-text">Checks against a data sample: {v.checks.join("; ")}</p>}
                <Code>{JSON.stringify(v.card, null, 2)}</Code>
              </Collapsible>
            ))}
        </Collapsible>
      )}

      {!!data.suggestions?.length && (
        <section aria-label="Suggestions">
          <h3>Suggestions from users</h3>
          <ul className="plain">
            {data.suggestions.map((s) => (
              <li key={s.id} className="small">
                <strong>{s.kind}</strong> from {s.raised_by}: {s.text} <span className="faint">{when(s.created_at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
