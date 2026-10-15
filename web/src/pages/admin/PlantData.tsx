import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type DatasetRow } from "../../api";
import { useSession } from "../../session";
import { ErrorNote } from "../../components/bits";
import CardReview from "../../components/CardReview";
import Icon from "../../components/icons";
import { usePlant } from "./PlantLayout";

export function datasetStatus(d: DatasetRow): { label: string; tone: string } {
  if (d.waiting_for_review) return { label: "Review", tone: "warn" };
  if (d.approved_version) return { label: "Approved", tone: "ok" };
  if (d.no_metadata_file) return { label: "No metadata file", tone: "bad" };
  return { label: "Draft", tone: "warn" };
}

/** Plant data sources: the list, the selected source's description for review, and registering a new one. */
export default function PlantData() {
  const { detail, reload } = usePlant();
  const [params, setParams] = useSearchParams();
  const selected = params.get("ds");
  const [adding, setAdding] = useState(false);

  return (
    <div className="split">
      <section aria-labelledby="ds" className="split-side-left stack-sm">
        <div className="spread">
          <h2 id="ds" className="h-panel">
            Data sources in the data lake
          </h2>
          <button type="button" className="ghost small" aria-expanded={adding} onClick={() => setAdding(!adding)}>
            <Icon name="plus" size={14} /> Add data source
          </button>
        </div>
        <p className="muted small">Each source is a folder in the data lake. Agents only use a source once you have approved its description.</p>
        {adding && (
          <RegisterForm
            plant={detail.plant.id}
            onDone={(id) => {
              setAdding(false);
              reload();
              setParams({ ds: id });
            }}
          />
        )}
        <div className="panel list">
          {detail.datasets.length === 0 && <p className="muted small pad-row">No data sources registered for this plant yet.</p>}
          {detail.datasets.map((d) => {
            const st = datasetStatus(d);
            return (
              <Link
                key={d.dataset}
                to={`?ds=${encodeURIComponent(d.dataset)}`}
                className={`list-row ${selected === d.dataset ? "active" : ""}`}
                aria-current={selected === d.dataset ? "true" : undefined}
              >
                <span className="spread">
                  <span className="mono accent-text">{d.dataset}</span>
                  <span className={`pill ${st.tone}`}>{st.label}</span>
                </span>
                <span className="spread small">
                  <span className="muted">{d.name && d.name !== d.dataset ? d.name : d.file_format}</span>
                  {d.runs_using !== null && <span className="faint">{d.runs_using ? `used by ${d.runs_using} ${d.runs_using === 1 ? "run" : "runs"}` : "not used yet"}</span>}
                </span>
              </Link>
            );
          })}
        </div>
      </section>
      <section aria-label="Description review" className="split-main panel pad">
        {selected ? (
          <CardReview key={selected} datasetId={selected} onChange={reload} />
        ) : (
          <p className="muted">Choose a data source to see what agents are told about it, and to review new versions.</p>
        )}
      </section>
    </div>
  );
}

function RegisterForm({ plant, onDone }: { plant: string; onDone: (id: string) => void }) {
  const { meta } = useSession();
  const formats = meta?.options.data_formats ?? ["parquet", "csv", "json"];
  const [f, setF] = useState({ id: "", name: "", s3_prefix: "", file_glob: "*.parquet", file_format: "parquet", metadata_uri: "", owner: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/datasets", { ...f, plant, name: f.name || null, metadata_uri: f.metadata_uri || null });
      await api.post(`/datasets/${f.id}:index`, {});
      onDone(f.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="panel pad stack-sm" onSubmit={submit} aria-label="Add data source">
      <p className="muted small">Point at the source's folder and its own metadata file; a description is drafted for you to review.</p>
      <div className="grid2">
        <label className="field">
          <span className="field-label">Id</span>
          <input className="mono" value={f.id} onChange={set("id")} pattern="[a-z][a-z0-9_]{1,62}" required placeholder="historian_line3" />
        </label>
        <label className="field">
          <span className="field-label">Name (optional)</span>
          <input value={f.name} onChange={set("name")} placeholder="Line 3 process tags" />
        </label>
      </div>
      <label className="field">
        <span className="field-label">Folder (prefix)</span>
        <input className="mono" value={f.s3_prefix} onChange={set("s3_prefix")} required placeholder={`s3://lake/plant=${plant}/historian/line3`} />
      </label>
      <div className="grid2">
        <label className="field">
          <span className="field-label">File glob</span>
          <input className="mono" value={f.file_glob} onChange={set("file_glob")} />
        </label>
        <label className="field">
          <span className="field-label">Format</span>
          <select value={f.file_format} onChange={set("file_format")}>
            {formats.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span className="field-label">Metadata file</span>
        <input className="mono" value={f.metadata_uri} onChange={set("metadata_uri")} placeholder={`s3://lake/plant=${plant}/historian/line3/_meta.yaml`} />
      </label>
      <label className="field">
        <span className="field-label">Owner</span>
        <input value={f.owner} onChange={set("owner")} />
      </label>
      <ErrorNote error={error} />
      <button className="primary" disabled={busy}>
        {busy ? "Registering…" : "Register and draft description"}
      </button>
    </form>
  );
}
