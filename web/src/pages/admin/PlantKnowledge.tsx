import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, DOC_TYPES, type DocDetail, type DocSummary, type FactDetail, type FactSummary, type Line } from "../../api";
import { useApi } from "../../session";
import { ErrorNote, Loading, day, when } from "../../components/bits";
import Icon from "../../components/icons";
import { usePlant } from "./PlantLayout";

/** Plant documents (SOPs, FMEAs, incident reports…) and reference facts, with user suggestions. */
export default function PlantKnowledge() {
  const { detail, reload } = usePlant();
  const [params, setParams] = useSearchParams();
  const doc = params.get("doc");
  const fact = params.get("fact");
  const [newDoc, setNewDoc] = useState(false);
  const [newFact, setNewFact] = useState(false);
  const plant = detail.plant.id;
  const base = `/admin/plants/${encodeURIComponent(plant)}`;
  const withParam = (k: "doc" | "fact", v: string | null) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    return p;
  };
  const open = (k: "doc" | "fact", v: string | null) => setParams(withParam(k, v));

  return (
    <div className="split">
      <div className="split-main stack">
        <section aria-labelledby="docs" className="stack-sm">
          <div className="row-baseline">
            <h2 id="docs" className="h-panel">
              Documents
            </h2>
            <span className="muted small">SOPs, FMEAs, incident reports, specs. Agents search them section by section and cite the version they used.</span>
          </div>
          {!newDoc && (
            <button type="button" className="dropzone" onClick={() => (setNewDoc(true), open("doc", null))}>
              <Icon name="upload" size={22} />
              <span>Add a document: upload a Markdown or text file, or paste its text. You set the type, lines and review date.</span>
            </button>
          )}
          {newDoc && (
            <DocForm
              plant={plant}
              lines={detail.lines}
              onCancel={() => setNewDoc(false)}
              onDone={(d) => {
                setNewDoc(false);
                reload();
                open("doc", d.id);
              }}
            />
          )}
          {doc && <DocViewer key={doc} id={doc} plant={plant} lines={detail.lines} onClose={() => open("doc", null)} onSaved={reload} />}
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Document</th>
                  <th scope="col">Type</th>
                  <th scope="col">Applies to</th>
                  <th scope="col">Version</th>
                  <th scope="col">Review by</th>
                </tr>
              </thead>
              <tbody>
                {detail.documents.length === 0 && (
                  <tr>
                    <td colSpan={5} className="muted">
                      No documents for this plant yet.
                    </td>
                  </tr>
                )}
                {detail.documents.map((d) => (
                  <tr key={d.id} className={doc === d.id ? "selected" : ""}>
                    <td>
                      <Link to={`?${withParam("doc", d.id)}`}>{d.title}</Link>
                      {d.read_only && <div className="faint tiny">Provided by a plugin</div>}
                    </td>
                    <td>{d.type}</td>
                    <td className="mono small">{d.lines.length ? d.lines.join(", ") : <span className="sans">All lines</span>}</td>
                    <td className="mono">v{d.version}</td>
                    <td className={d.overdue ? "warn-text" : "muted"}>
                      {d.review_date ? (d.overdue ? `Overdue since ${day(d.review_date)}` : day(d.review_date)) : "No date"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <aside className="split-side stack">
        <section aria-labelledby="facts" className="panel">
          <div className="panel-head column">
            <h2 id="facts" className="h-panel">
              Reference facts
            </h2>
            <span className="muted small">
              Exact values the analysis code uses directly, such as spec limits and residence times. Each run keeps a copy, so replays use the same
              numbers.
            </span>
          </div>
          {detail.facts.length === 0 && <p className="muted small pad-row">No fact tables yet.</p>}
          {detail.facts.map((f) => (
            <Link key={f.name} to={`?${withParam("fact", f.name)}`} className={`list-row ${fact === f.name ? "active" : ""}`}>
              <span className="spread">
                <span className="mono accent-text">{f.name}</span>
                <span className="mono faint small">v{f.version}</span>
              </span>
              <span className="small muted">
                {f.description}
                {f.read_only && " · provided by a plugin"}
              </span>
            </Link>
          ))}
          <div className="pad-row">
            {!newFact && (
              <button type="button" className="ghost small" onClick={() => (setNewFact(true), open("fact", null))}>
                <Icon name="plus" size={14} /> Add a fact table
              </button>
            )}
          </div>
        </section>
        {newFact && (
          <FactEditor
            plant={plant}
            onCancel={() => setNewFact(false)}
            onSaved={(f) => {
              setNewFact(false);
              reload();
              open("fact", f.name);
            }}
          />
        )}
        {fact && <FactViewer key={fact} name={fact} plant={plant} onClose={() => open("fact", null)} onSaved={reload} />}
        <section aria-labelledby="sg" className="panel pad stack-sm">
          <h2 id="sg" className="h-panel">
            Suggestions from users
          </h2>
          {detail.suggestions.length === 0 && <p className="muted small">No suggestions.</p>}
          {detail.suggestions.map((s) => (
            <div key={s.id} className="subcard">
              <div className="small">
                <strong>{s.raised_by}</strong> ({s.kind}): {s.text}
              </div>
              <div className="small faint">
                About <span className="mono">{s.dataset_id}</span> · {day(s.created_at)} · {s.status ?? "open"}
              </div>
              <Link className="small" to={`${base}/data?ds=${encodeURIComponent(s.dataset_id)}`}>
                Open its description
              </Link>
            </div>
          ))}
        </section>
      </aside>
    </div>
  );
}

// ------------------------------------------------------------------ documents

function DocViewer({ id, plant, lines, onClose, onSaved }: { id: string; plant: string; lines: Line[]; onClose: () => void; onSaved: () => void }) {
  const { data, error, reload } = useApi<DocDetail>(`/admin/documents/${encodeURIComponent(id)}`);
  const [editing, setEditing] = useState(false);
  return (
    <section className="panel pad stack-sm" aria-label="Document">
      <div className="spread">
        <h3 className="h-panel">{data?.title ?? id}</h3>
        <button type="button" className="icon-btn" aria-label="Close document" onClick={onClose}>
          <Icon name="x" />
        </button>
      </div>
      <ErrorNote error={error} />
      {!data && !error && <Loading />}
      {data && !editing && (
        <>
          <div className="small muted">
            <span className="mono">{data.id}</span> · {data.type} · v{data.version} · {data.sections} sections
            {data.updated_at && ` · updated ${when(data.updated_at)}`}
            {data.updated_by && ` by ${data.updated_by}`}
            {data.review_date && (
              <span className={data.overdue ? "warn-text" : ""}> · review by {day(data.review_date)}</span>
            )}
          </div>
          {data.read_only ? (
            <div className="callout small">Provided by a plugin knowledge pack. Change it in the plugin, not here.</div>
          ) : (
            <div>
              <button type="button" className="outline small" onClick={() => setEditing(true)}>
                Save new version
              </button>
            </div>
          )}
          <pre className="doc-text">{data.content}</pre>
        </>
      )}
      {data && editing && (
        <DocForm
          plant={plant}
          lines={lines}
          existing={data}
          onCancel={() => setEditing(false)}
          onDone={() => {
            setEditing(false);
            reload();
            onSaved();
          }}
        />
      )}
    </section>
  );
}

function DocForm({
  plant,
  lines,
  existing,
  onDone,
  onCancel,
}: {
  plant: string;
  lines: Line[];
  existing?: DocDetail;
  onDone: (d: DocSummary) => void;
  onCancel: () => void;
}) {
  const [f, setF] = useState({
    title: existing?.title ?? "",
    type: existing?.type ?? "SOP",
    lines: existing?.lines ?? ([] as string[]),
    review_date: existing?.review_date ?? "",
    content: existing?.content ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const readFile = (file: File | undefined) => {
    if (!file) return;
    if (!/\.(md|markdown|txt)$/i.test(file.name)) {
      setError("Only Markdown (.md, .markdown) or plain text (.txt) files can be read here. Paste other documents as text.");
      return;
    }
    const r = new FileReader();
    r.onload = () =>
      setF((x) => ({ ...x, content: String(r.result ?? ""), title: x.title || file.name.replace(/\.(md|markdown|txt)$/i, "") }));
    r.onerror = () => setError(`Could not read ${file.name}.`);
    r.readAsText(file);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const d = await api.post<DocSummary>("/admin/documents", { ...(existing ? { id: existing.id } : {}), plant, ...f });
      onDone(d);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="panel pad stack-sm"
      onSubmit={submit}
      aria-label={existing ? "Save a new version" : "Add a document"}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        readFile(e.dataTransfer.files[0]);
      }}
    >
      <h3 className="h-panel">{existing ? `New version of ${existing.title} (now v${existing.version})` : "Add a document"}</h3>
      <label className="field">
        <span className="field-label">File (Markdown or text), or drop it on this form</span>
        <input type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" onChange={(e) => readFile(e.target.files?.[0])} />
      </label>
      <div className="grid2">
        <label className="field">
          <span className="field-label">Title</span>
          <input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required />
        </label>
        <label className="field">
          <span className="field-label">Type</span>
          <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
            {DOC_TYPES.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
        </label>
      </div>
      <fieldset className="box">
        <legend>Applies to lines</legend>
        {lines.length === 0 && <p className="faint small">No lines defined for this plant; the document applies to all of it.</p>}
        <div className="plant-chips">
          {lines.map((l) => (
            <label key={l.id} className={`pchip ${f.lines.includes(l.id) ? "on" : ""}`}>
              <input
                type="checkbox"
                checked={f.lines.includes(l.id)}
                onChange={(e) => setF({ ...f, lines: e.target.checked ? [...f.lines, l.id] : f.lines.filter((x) => x !== l.id) })}
              />
              <span className="mono">{l.id}</span>
              {l.name && <span className="muted small">{l.name}</span>}
            </label>
          ))}
        </div>
        {lines.length > 0 && <p className="faint small">None ticked means all lines.</p>}
      </fieldset>
      <label className="field narrow-field">
        <span className="field-label">Review by</span>
        <input type="date" className="mono" value={f.review_date} onChange={(e) => setF({ ...f, review_date: e.target.value })} />
      </label>
      <label className="field">
        <span className="field-label">Text (Markdown; headings split it into the sections agents search)</span>
        <textarea className="code" rows={14} value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} required />
      </label>
      <ErrorNote error={error} />
      <div className="row-center">
        <button className="primary" disabled={busy}>
          {busy ? "Saving…" : existing ? "Save new version" : "Add document"}
        </button>
        <button type="button" className="ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------ facts

function FactViewer({ name, plant, onClose, onSaved }: { name: string; plant: string; onClose: () => void; onSaved: () => void }) {
  const { data, error, reload } = useApi<FactDetail>(`/admin/facts/${encodeURIComponent(name)}`);
  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  return (
    <FactEditor
      plant={plant}
      existing={data}
      onCancel={onClose}
      onSaved={() => {
        reload();
        onSaved();
      }}
    />
  );
}

function FactEditor({ plant, existing, onSaved, onCancel }: { plant: string; existing?: FactDetail; onSaved: (f: FactSummary) => void; onCancel: () => void }) {
  const [f, setF] = useState({ name: existing?.name ?? "", description: existing?.description ?? "", content: existing?.content ?? "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ro = !!existing?.read_only;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const saved = await api.post<FactSummary>("/admin/facts", { plant, ...f });
      setMsg(`Saved v${saved.version}.`);
      onSaved(saved);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="panel pad stack-sm" onSubmit={submit} aria-label={existing ? `Fact table ${existing.name}` : "Add a fact table"}>
      <div className="spread">
        <h3 className="h-panel">{existing ? <span className="mono">{existing.name}</span> : "Add a fact table"}</h3>
        <button type="button" className="icon-btn" aria-label="Close" onClick={onCancel}>
          <Icon name="x" />
        </button>
      </div>
      {existing && (
        <div className="small muted">
          v{existing.version}
          {existing.updated_at && ` · updated ${when(existing.updated_at)}`}
          {existing.updated_by && ` by ${existing.updated_by}`}
        </div>
      )}
      {ro && <div className="callout small">Provided by a plugin knowledge pack. Change it in the plugin, not here.</div>}
      {!existing && (
        <label className="field">
          <span className="field-label">Name</span>
          <input
            className="mono"
            value={f.name}
            onChange={(e) => setF({ ...f, name: e.target.value })}
            pattern="[a-z][a-z0-9_]{1,62}"
            title="lower-case letters, digits and underscore, starting with a letter"
            required
            placeholder="spec_limits"
          />
        </label>
      )}
      <label className="field">
        <span className="field-label">Description</span>
        <input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} readOnly={ro} />
      </label>
      <label className="field">
        <span className="field-label">Values (YAML)</span>
        <textarea className="code" rows={14} value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} readOnly={ro} spellCheck={false} required />
      </label>
      <ErrorNote error={error} />
      {msg && (
        <p className="ok-text small" role="status">
          {msg}
        </p>
      )}
      {!ro && (
        <div className="row-center">
          <button className="primary" disabled={busy}>
            {busy ? "Saving…" : existing ? "Save new version" : "Add fact table"}
          </button>
        </div>
      )}
    </form>
  );
}
