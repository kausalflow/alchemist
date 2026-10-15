import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type DatasetDetail } from "../api";
import { useApi, useSession } from "../session";
import { ErrorNote, Loading, when } from "../components/bits";
import CardReview, { ExposurePill } from "../components/CardReview";

export default function DatasetPage() {
  const { datasetId } = useParams();
  const { me } = useSession();
  const { data, error, reload } = useApi<DatasetDetail>(`/datasets/${datasetId}`);
  if (error)
    return (
      <div className="page section">
        <ErrorNote error={error} />
      </div>
    );
  if (!data) return <Loading />;
  const ds = data.dataset;
  const s = data.summary;
  const card = data.approved?.card;

  return (
    <div className="page">
      <header className="page-head">
        <div className="grow">
          <div className="crumb">
            <Link to="/catalog">Data catalog</Link> / <span className="mono">{ds.plant}</span>
          </div>
          <h1 className="mono">{ds.id}</h1>
          <div className="small muted">
            Owner {ds.owner || "unknown"} · <span className="mono">{ds.file_format}</span> · sensitivity {ds.sensitivity}
            {data.approved && (
              <>
                {" "}
                · card v{data.approved.version} approved by {data.approved.reviewed_by} {when(data.approved.reviewed_at)}
              </>
            )}
          </div>
        </div>
      </header>
      <div className="section stack">
        {!s && <div className="callout warn">No approved card yet, so runs cannot query this data source.</div>}
        {s && (
          <>
            {s.description && <p className="lead measure">{s.description}</p>}
            <dl className="facts wide panel pad">
              <dt>View for queries</dt>
              <dd className="mono">{s.view}</dd>
              <dt>One row is</dt>
              <dd>{s.grain}</dd>
              <dt>Time zone</dt>
              <dd>
                <span className="mono">{s.time_zone}</span> {s.time_meaning && <span className="muted">· {s.time_meaning}</span>}
              </dd>
              <dt>Join keys</dt>
              <dd className="mono">{s.join_keys?.join(", ")}</dd>
              {!!s.quirks?.length && (
                <>
                  <dt>Known quirks</dt>
                  <dd>
                    <ul className="bullets">
                      {s.quirks.map((q) => (
                        <li key={q}>{q}</li>
                      ))}
                    </ul>
                  </dd>
                </>
              )}
              {!!s.missing_facts?.length && (
                <>
                  <dt>Missing facts</dt>
                  <dd className="warn-text">{s.missing_facts.join("; ")}</dd>
                </>
              )}
            </dl>
            <h2 className="h-section">Columns</h2>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Column</th>
                    <th scope="col">Type</th>
                    <th scope="col">Unit</th>
                    <th scope="col">Valid range</th>
                    <th scope="col">Description</th>
                    <th scope="col">Can the AI model see values?</th>
                  </tr>
                </thead>
                <tbody>
                  {(card?.columns ?? []).map((c) => (
                    <tr key={c.name}>
                      <td className="mono">{c.name}</td>
                      <td className="small mono">{c.type}</td>
                      <td>{c.unit}</td>
                      <td className="small mono">{c.valid_min !== undefined && c.valid_min !== null ? `${c.valid_min} to ${c.valid_max}` : ""}</td>
                      <td className="small">{c.description}</td>
                      <td>
                        <ExposurePill exposure={c.exposure} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        <SuggestForm id={ds.id} onDone={reload} />
        {me!.catalog_admin && data.versions && (
          <section className="panel pad" aria-label="Card review">
            <p className="eyebrow plan-text">Catalog admin</p>
            <CardReview datasetId={ds.id} onChange={reload} />
          </section>
        )}
      </div>
    </div>
  );
}

function SuggestForm({ id, onDone }: { id: string; onDone: () => void }) {
  const [kind, setKind] = useState("other");
  const [text, setText] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.post(`/datasets/${id}/suggestions`, { kind, text });
      setSent(true);
      setText("");
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <form className="panel pad stack-sm" onSubmit={send}>
      <h2 className="h-panel">Suggest a fix to this card</h2>
      <p className="muted small">Catalog admins review suggestions; nothing changes until they approve a new card version.</p>
      <div className="row">
        <label>
          <span className="sr-only">Kind of fix</span>
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {["description", "unit", "quirk", "column", "other"].map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>
        <label className="grow">
          <span className="sr-only">What is wrong or missing?</span>
          <input value={text} onChange={(e) => (setText(e.target.value), setSent(false))} placeholder="What is wrong or missing?" />
        </label>
        <button disabled={text.length < 3}>Send</button>
      </div>
      {sent && (
        <p className="ok-text small" role="status">
          Thanks, sent to the catalog admins.
        </p>
      )}
      <ErrorNote error={error} />
    </form>
  );
}
