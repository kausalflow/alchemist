import { useEffect, useRef, useState } from "react";
import { api, uploadsPath, type Upload, type UploadTable } from "../api";
import { Collapsible, ErrorNote, day } from "./bits";

const ACCEPT = ".xlsx,.xlsm,.xls,.csv,.tsv";

function size(bytes: number) {
  return bytes > 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} kB`;
}

/** Data files on the Ask page: upload Excel or CSV files to the project, and pick which ones the question uses. */
export default function DataFiles({
  workspace,
  project,
  plants,
  me,
  selected,
  onSelected,
}: {
  workspace: string;
  project: string;
  plants: string[];
  me: string;
  selected: string[];
  onSelected: (ids: string[]) => void;
}) {
  const [files, setFiles] = useState<Upload[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [plant, setPlant] = useState("");
  const [timeZone, setTimeZone] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const ready = !!workspace && /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(project);

  useEffect(() => {
    setFiles([]);
    if (!ready) return;
    const t = setTimeout(() => {
      api.get<Upload[]>(uploadsPath(workspace, project)).then(setFiles).catch(() => setFiles([]));
    }, 300);
    return () => clearTimeout(t);
  }, [workspace, project, ready]);

  const add = async (list: FileList | null) => {
    if (!list?.length) return;
    setError(null);
    const added: string[] = [];
    for (const f of Array.from(list)) {
      setBusy(`Reading ${f.name}…`);
      try {
        const params: Record<string, string> = { filename: f.name };
        if (plant) params.plant = plant;
        if (timeZone) params.time_zone = timeZone;
        const rec = await api.upload<Upload>(uploadsPath(workspace, project), f, params);
        added.push(rec.id);
        setFiles((prev) => [rec, ...prev]);
      } catch (err) {
        setError(`${f.name}: ${(err as Error).message}`);
      }
    }
    setBusy(null);
    if (added.length) onSelected([...selected, ...added]);
    if (input.current) input.current.value = "";
  };

  const toggle = (id: string, on: boolean) => onSelected(on ? [...selected, id] : selected.filter((x) => x !== id));

  return (
    <fieldset className="box">
      <legend>Data files (optional)</legend>
      <p className="muted small">
        Upload Excel or CSV files to analyse with the catalog data. Each sheet becomes a table the agent queries as{" "}
        <span className="mono">ds.up_…</span>; the file is kept unchanged with the run's provenance.
      </p>
      {!ready ? (
        <p className="faint small">Name the project below first: files belong to a project, so later questions can use them too.</p>
      ) : (
        <>
          <div className="grid3">
            <label className="field">
              <span className="field-label">Plant whose data it is</span>
              <select value={plant} onChange={(e) => setPlant(e.target.value)}>
                <option value="">Not plant data</option>
                {plants.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Time zone of its timestamps</span>
              <input className="mono" value={timeZone} onChange={(e) => setTimeZone(e.target.value)} placeholder="Europe/Berlin" />
            </label>
            <label className="field">
              <span className="field-label">File</span>
              <input
                ref={input}
                type="file"
                accept={ACCEPT}
                multiple
                disabled={!!busy}
                onChange={(e) => add(e.target.files)}
                aria-label="Upload Excel or CSV files"
              />
            </label>
          </div>
          {busy && <p className="small muted">{busy}</p>}
          <ErrorNote error={error} />
          {files.length > 0 && (
            <div className="stack-sm">
              {files.map((u) => (
                <FileCard
                  key={u.id}
                  upload={u}
                  workspace={workspace}
                  project={project}
                  mine={u.uploaded_by === me}
                  checked={selected.includes(u.id)}
                  onToggle={(on) => toggle(u.id, on)}
                  onChange={(rec) => setFiles((prev) => prev.map((x) => (x.id === rec.id ? rec : x)))}
                />
              ))}
            </div>
          )}
        </>
      )}
    </fieldset>
  );
}

function FileCard({
  upload: u,
  workspace,
  project,
  mine,
  checked,
  onToggle,
  onChange,
}: {
  upload: Upload;
  workspace: string;
  project: string;
  mine: boolean;
  checked: boolean;
  onToggle: (on: boolean) => void;
  onChange: (u: Upload) => void;
}) {
  return (
    <div className="subcard">
      <label className="row-center">
        <input type="checkbox" checked={checked} onChange={(e) => onToggle(e.target.checked)} />
        <span className="strong-ish">{u.file.name}</span>
        <span className="small muted">
          {size(u.file.size)} · {mine ? "you" : u.uploaded_by} · {day(u.uploaded_at)}
          {u.plant ? <> · plant <span className="mono">{u.plant}</span></> : null}
        </span>
        <a className="small" href={`/api${uploadsPath(workspace, project)}/${u.id}/original`}>
          Download
        </a>
      </label>
      {u.tables.map((t) => (
        <TableInfo key={t.name} upload={u} table={t} workspace={workspace} project={project} mine={mine} onChange={onChange} />
      ))}
      {u.skipped.map((s) => (
        <div key={s.sheet} className="small faint">
          Sheet {s.sheet} skipped: {s.reason}
        </div>
      ))}
    </div>
  );
}

function TableInfo({
  upload,
  table: t,
  workspace,
  project,
  mine,
  onChange,
}: {
  upload: Upload;
  table: UploadTable;
  workspace: string;
  project: string;
  mine: boolean;
  onChange: (u: Upload) => void;
}) {
  const [preview, setPreview] = useState<Upload["previews"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    if (!preview)
      api
        .get<Upload>(`${uploadsPath(workspace, project)}/${upload.id}`)
        .then((r) => setPreview(r.previews ?? {}))
        .catch((e) => setError((e as Error).message));
  };
  const hide = async (column: string, hidden: boolean) => {
    setError(null);
    try {
      const rec = await api.put<Upload>(`${uploadsPath(workspace, project)}/${upload.id}/exposure`, {
        table: t.name,
        column,
        exposure: hidden ? "never_to_model" : "model",
      });
      onChange(rec);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const p = preview?.[t.name];
  const shown = t.columns.filter((c) => !c.name.endsWith("__raw"));
  return (
    <div className="small">
      <div>
        <span className="mono">ds.{t.name}</span>
        {t.sheet ? <> · sheet {t.sheet}</> : null} · {t.rows.toLocaleString()} rows · {shown.length} columns
        {t.header_row ? <> · header in row {t.header_row}</> : <> · no header row</>}
      </div>
      {t.notes.length > 0 && (
        <ul className="bullets small muted">
          {t.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      <div onClick={load}>
        <Collapsible title="Columns and first rows">
          <ErrorNote error={error} />
          <table className="table small">
            <thead>
              <tr>
                <th>Column</th>
                <th>In the file</th>
                <th>Type</th>
                <th>Unit</th>
                <th>Model sees it</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((c) => (
                <tr key={c.name}>
                  <td className="mono">{c.name}</td>
                  <td>{c.original}</td>
                  <td className="mono">{c.type}</td>
                  <td>{c.unit ?? ""}</td>
                  <td>
                    <input
                      type="checkbox"
                      checked={c.exposure !== "never_to_model"}
                      disabled={!mine}
                      title={mine ? "Untick to keep this column in the sandbox only" : "Only the person who uploaded the file can change this"}
                      onChange={(e) => hide(c.name, !e.target.checked)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {p && (
            <div className="scrollx">
              <table className="table small">
                <thead>
                  <tr>
                    {p.columns.map((c) => (
                      <th key={c} className="mono">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {p.head.map((row, i) => (
                    <tr key={i}>
                      {p.columns.map((c) => (
                        <td key={c}>{row[c] === null || row[c] === undefined ? "" : String(row[c])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Collapsible>
      </div>
    </div>
  );
}
