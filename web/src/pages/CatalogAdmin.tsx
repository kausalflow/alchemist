import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApi, useSession } from "../session";
import { Chip, ErrorNote, when } from "../components/bits";

interface Health {
  dataset: string;
  plant: string;
  approved_version: number | null;
  latest_version: number | null;
  waiting_for_review: boolean;
  missing_facts: string[];
  checks: string[];
  no_metadata_file: boolean;
}

export default function CatalogAdminPage() {
  const { me } = useSession();
  const { data: health, error, reload } = useApi<Health[]>(me!.catalog_admin ? "/catalog/health" : null);
  const { data: suggestions } = useApi<{ id: number; dataset_id: string; raised_by: string; kind: string; text: string; created_at: string }[]>(
    me!.catalog_admin ? "/catalog/suggestions" : null,
  );
  const [msg, setMsg] = useState<string | null>(null);
  if (!me!.catalog_admin) return <p>Catalog admins only.</p>;

  const reindex = async (id: string) => {
    setMsg(null);
    try {
      const r = await api.post<{ status: string; version: number }>(`/datasets/${id}:index`, { force: true });
      setMsg(`${id}: ${r.status} v${r.version}`);
      reload();
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  return (
    <div>
      <h1>Catalog admin</h1>
      <h2>Health</h2>
      <ErrorNote error={error} />
      {msg && <p className="small">{msg}</p>}
      <table className="table">
        <thead>
          <tr>
            <th>Dataset</th>
            <th>Plant</th>
            <th>Approved</th>
            <th>Waiting for review</th>
            <th>Missing facts</th>
            <th>Sample checks</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {health?.map((h) => (
            <tr key={h.dataset}>
              <td>
                <Link to={`/catalog/${h.dataset}`}>{h.dataset}</Link>
                {h.no_metadata_file && <Chip tone="warn">no metadata file</Chip>}
              </td>
              <td>{h.plant}</td>
              <td>{h.approved_version ? `v${h.approved_version}` : <Chip tone="bad">none</Chip>}</td>
              <td>{h.waiting_for_review ? <Chip tone="warn">v{h.latest_version}</Chip> : ""}</td>
              <td className="small">{h.missing_facts.join("; ")}</td>
              <td className="small">{h.checks.join("; ")}</td>
              <td>
                <button className="small" onClick={() => reindex(h.dataset)}>
                  Re-draft card
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2>Suggestions</h2>
      {suggestions?.length === 0 && <p className="muted">No suggestions.</p>}
      <ul>
        {suggestions?.map((s) => (
          <li key={s.id}>
            <Link to={`/catalog/${s.dataset_id}`}>{s.dataset_id}</Link> · <strong>{s.kind}</strong> from {s.raised_by}: {s.text}{" "}
            <span className="muted small">{when(s.created_at)}</span>
          </li>
        ))}
      </ul>
      <RegisterForm onDone={reload} />
    </div>
  );
}

function RegisterForm({ onDone }: { onDone: () => void }) {
  const { meta } = useSession();
  const formats = meta?.options.data_formats ?? ["parquet", "csv", "json"];
  const [f, setF] = useState({ id: "", s3_prefix: "", file_glob: "*.parquet", file_format: "parquet", plant: "", metadata_uri: "", owner: "" });
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/datasets", { ...f, metadata_uri: f.metadata_uri || null });
      const r = await api.post<{ version: number }>(`/datasets/${f.id}:index`, {});
      setOk(`Registered ${f.id}; card v${r.version} drafted for review.`);
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <form className="card form" onSubmit={submit}>
      <h2>Register a dataset</h2>
      <p className="muted small">Point at the dataset's prefix and its own metadata file; a card is drafted for you to review.</p>
      <div className="row">
        <label>
          Id
          <input value={f.id} onChange={set("id")} pattern="[a-z][a-z0-9_]+" required placeholder="historian_line3" />
        </label>
        <label>
          Plant
          <input value={f.plant} onChange={set("plant")} required placeholder="P01" />
        </label>
        <label>
          Format
          <select value={f.file_format} onChange={set("file_format")}>
            {formats.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
      </div>
      <label>
        Prefix
        <input value={f.s3_prefix} onChange={set("s3_prefix")} required placeholder="s3://lake/plant=P01/historian/line3" />
      </label>
      <div className="row">
        <label>
          File glob
          <input value={f.file_glob} onChange={set("file_glob")} />
        </label>
        <label className="grow">
          Metadata file
          <input value={f.metadata_uri} onChange={set("metadata_uri")} placeholder="s3://lake/plant=P01/historian/line3/_meta.yaml" />
        </label>
        <label>
          Owner
          <input value={f.owner} onChange={set("owner")} />
        </label>
      </div>
      <ErrorNote error={error} />
      {ok && <p className="ok">{ok}</p>}
      <button className="primary">Register and draft card</button>
    </form>
  );
}
