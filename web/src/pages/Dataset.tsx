import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type DatasetSummary } from "../api";
import { useApi, useSession } from "../session";
import { Chip, Code, Collapsible, ErrorNote, when } from "../components/bits";

interface CardVersion {
  dataset_id: string;
  version: number;
  status: string;
  drafted_by: string;
  reviewed_by?: string;
  card: Record<string, any>;
  checks: string[];
}

interface DatasetDetail {
  dataset: Record<string, string>;
  approved: { version: number; card: Record<string, any>; reviewed_by: string; reviewed_at: string } | null;
  summary: DatasetSummary | null;
  versions?: CardVersion[];
  suggestions?: { id: number; raised_by: string; kind: string; text: string; status: string; created_at: string }[];
}

export default function DatasetPage() {
  const { datasetId } = useParams();
  const { me } = useSession();
  const { data, error, reload } = useApi<DatasetDetail>(`/datasets/${datasetId}`);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <p className="muted">Loading…</p>;
  const ds = data.dataset;
  const s = data.summary;
  const card = data.approved?.card;

  return (
    <div>
      <p className="small">
        <Link to="/catalog">Catalog</Link> /
      </p>
      <h1>{ds.id}</h1>
      <p className="muted small">
        plant {ds.plant} · owner {ds.owner || "unknown"} · {ds.file_format} · sensitivity {ds.sensitivity}
        {data.approved && (
          <>
            {" "}
            · card v{data.approved.version} approved by {data.approved.reviewed_by} {when(data.approved.reviewed_at)}
          </>
        )}
      </p>
      {!s && <p className="card warn">No approved card yet, so runs cannot query this dataset.</p>}
      {s && (
        <>
          <p>{s.description}</p>
          <table className="table kv">
            <tbody>
              <tr>
                <th>View for queries</th>
                <td>
                  <code>{s.view}</code>
                </td>
              </tr>
              <tr>
                <th>Grain</th>
                <td>{s.grain}</td>
              </tr>
              <tr>
                <th>Time zone</th>
                <td>
                  {s.time_zone} {s.time_meaning && <span className="muted">· {s.time_meaning}</span>}
                </td>
              </tr>
              <tr>
                <th>Join keys</th>
                <td>{s.join_keys?.join(", ")}</td>
              </tr>
              <tr>
                <th>Known quirks</th>
                <td>
                  <ul className="plain">
                    {s.quirks?.map((q) => (
                      <li key={q}>{q}</li>
                    ))}
                  </ul>
                </td>
              </tr>
              {!!s.missing_facts?.length && (
                <tr>
                  <th>Missing facts</th>
                  <td>{s.missing_facts.join("; ")}</td>
                </tr>
              )}
            </tbody>
          </table>
          <h3>Columns</h3>
          <table className="table">
            <thead>
              <tr>
                <th>Column</th>
                <th>Type</th>
                <th>Unit</th>
                <th>Valid range</th>
                <th>Description</th>
                <th>Model sees it</th>
              </tr>
            </thead>
            <tbody>
              {(card?.columns ?? []).map((c: any) => (
                <tr key={c.name}>
                  <td>
                    <code>{c.name}</code>
                  </td>
                  <td className="small">{c.type}</td>
                  <td>{c.unit}</td>
                  <td className="small">{c.valid_min !== undefined ? `${c.valid_min} to ${c.valid_max}` : ""}</td>
                  <td className="small">{c.description}</td>
                  <td>
                    {c.exposure === "never_to_model" ? (
                      <Chip tone="bad">hidden</Chip>
                    ) : c.exposure === "pseudonymise" ? (
                      <Chip tone="warn">pseudonymised</Chip>
                    ) : (
                      "yes"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <SuggestForm id={ds.id} onDone={reload} />
      {me!.catalog_admin && data.versions && <AdminVersions data={data} reload={reload} />}
    </div>
  );
}

function SuggestForm({ id, onDone }: { id: string; onDone: () => void }) {
  const [kind, setKind] = useState("other");
  const [text, setText] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const send = async () => {
    try {
      await api.post(`/datasets/${id}/suggestions`, { kind, text });
      setSent(true);
      setText("");
      onDone();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <section className="card">
      <h3>Suggest a fix to this card</h3>
      <p className="muted small">Catalog admins review suggestions; nothing changes until they approve a new card version.</p>
      <div className="row">
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {["description", "unit", "quirk", "column", "other"].map((k) => (
            <option key={k}>{k}</option>
          ))}
        </select>
        <input className="grow" value={text} onChange={(e) => (setText(e.target.value), setSent(false))} placeholder="What is wrong or missing?" />
        <button onClick={send} disabled={text.length < 3}>
          Send
        </button>
      </div>
      {sent && <p className="ok small">Thanks, sent to the catalog admins.</p>}
      <ErrorNote error={error} />
    </section>
  );
}

function AdminVersions({ data, reload }: { data: DatasetDetail; reload: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [edit, setEdit] = useState<{ version: number; text: string } | null>(null);
  const act = async (v: number, action: "approve" | "reject", card?: unknown) => {
    setError(null);
    try {
      await api.post(`/datasets/${data.dataset.id}/cards/${v}:${action}`, action === "approve" ? { card: card ?? null } : {});
      setEdit(null);
      reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <section>
      <h2>Card versions (catalog admin)</h2>
      <ErrorNote error={error} />
      {data.versions!.slice().reverse().map((v) => (
        <Collapsible
          key={v.version}
          open={v.status === "draft"}
          title={
            <>
              v{v.version} <Chip tone={v.status === "approved" ? "ok" : v.status === "draft" ? "warn" : ""}>{v.status}</Chip>{" "}
              <span className="muted small">
                drafted by {v.drafted_by}
                {v.reviewed_by && ` · reviewed by ${v.reviewed_by}`}
              </span>
            </>
          }
        >
          {v.checks.length > 0 && <p className="warn small">Checks against a data sample: {v.checks.join("; ")}</p>}
          {!!v.card.missing?.length && <p className="small">Missing facts: {v.card.missing.join("; ")}</p>}
          {edit?.version === v.version ? (
            <textarea className="code" rows={20} value={edit.text} onChange={(e) => setEdit({ ...edit, text: e.target.value })} />
          ) : (
            <Code>{JSON.stringify(v.card, null, 2)}</Code>
          )}
          {v.status === "draft" && (
            <div className="buttons">
              {edit?.version === v.version ? (
                <button
                  className="primary"
                  onClick={() => {
                    try {
                      act(v.version, "approve", JSON.parse(edit.text));
                    } catch {
                      setError("The edited card is not valid JSON.");
                    }
                  }}
                >
                  Approve edited card
                </button>
              ) : (
                <>
                  <button className="primary" onClick={() => act(v.version, "approve")}>
                    Approve
                  </button>
                  <button onClick={() => setEdit({ version: v.version, text: JSON.stringify(v.card, null, 2) })}>Edit, then approve</button>
                </>
              )}
              <button className="danger" onClick={() => act(v.version, "reject")}>
                Reject
              </button>
            </div>
          )}
        </Collapsible>
      ))}
      {!!data.suggestions?.length && (
        <>
          <h3>Suggestions</h3>
          <ul>
            {data.suggestions.map((s) => (
              <li key={s.id}>
                <strong>{s.kind}</strong> from {s.raised_by}: {s.text} <span className="muted small">{when(s.created_at)}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
