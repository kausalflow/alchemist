import { useState } from "react";
import { api, documentsPath, type ProjectDocument } from "../api";
import { Collapsible, ErrorNote, day } from "./bits";

function size(bytes: number) {
  return bytes > 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} kB`;
}

/** One document in a project: what it is, the text Alchemist reads from it, and a way to take it out. */
export default function DocumentCard({
  doc: d,
  workspace,
  project,
  me,
  onRemoved,
}: {
  doc: ProjectDocument;
  workspace: string;
  project: string;
  me: string;
  onRemoved: (id: string) => void;
}) {
  const [text, setText] = useState<ProjectDocument | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = `${documentsPath(workspace, project)}/${d.id}`;
  const load = () => {
    if (!text)
      api
        .get<ProjectDocument>(base)
        .then(setText)
        .catch((e) => setError((e as Error).message));
  };
  const remove = async () => {
    setError(null);
    try {
      await api.del<ProjectDocument>(base);
      onRemoved(d.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const pages = d.pages ? `${d.pages} ${d.file.format === "pptx" ? "slides" : "pages"} · ` : "";
  return (
    <div className="subcard">
      <div className="row-center">
        <span className="strong-ish">{d.file.name}</span>
        <span className="small muted">
          {pages}
          {size(d.file.size)} · {d.added_by === me ? "you" : d.added_by} · {day(d.added_at)}
        </span>
        <a className="small" href={`/api${base}/original`}>
          Download
        </a>
        {confirm ? (
          <span className="row-center small">
            Take it out of the project?
            <button type="button" className="outline small" onClick={remove}>
              Yes, take out
            </button>
            <button type="button" className="ghost small" onClick={() => setConfirm(false)}>
              Keep
            </button>
          </span>
        ) : (
          <button type="button" className="ghost small" onClick={() => setConfirm(true)}>
            Take out
          </button>
        )}
      </div>
      {d.empty_pages && d.empty_pages.length > 0 && (
        <div className="small muted">
          No text on {d.empty_pages.length === 1 ? "page" : "pages"} {d.empty_pages.join(", ")}: scanned pages and pictures
          are not read.
        </div>
      )}
      <ErrorNote error={error} />
      <div onClick={load}>
        <Collapsible title="Text Alchemist reads">
          {!text && <p className="small faint">Loading…</p>}
          {text && (
            <>
              <pre className="doc-text small">{text.text}</pre>
              {text.truncated && (
                <p className="small faint">Showing the start of {d.chars.toLocaleString()} characters.</p>
              )}
            </>
          )}
        </Collapsible>
      </div>
    </div>
  );
}
