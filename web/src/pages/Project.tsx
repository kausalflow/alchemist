import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type RunSummary } from "../api";
import { useApi, useSession } from "../session";
import { useWorkspace } from "../workspace";
import { ErrorNote, Loading, StatusChip, relTime, when } from "../components/bits";
import DataFiles from "../components/DataFiles";
import Icon from "../components/icons";

interface Project {
  workspace: string;
  name: string;
  exists: boolean;
  owner?: string | null;
  created_at?: string | null;
  background: string;
  background_updated_by?: string | null;
  background_updated_at?: string | null;
}

const projectPath = (ws: string, p: string) => `/projects/${encodeURIComponent(ws)}/${encodeURIComponent(p)}`;

/** A project's home, like a project in a chat app: the background Alchemist reads before every question here, the
 * data files, and every question asked in it. */
export default function ProjectPage() {
  const { workspace = "", project = "" } = useParams();
  const { me } = useSession();
  const { workspaces, reload: reloadWorkspaces } = useWorkspace();
  const { data: proj, error, setData } = useApi<Project>(projectPath(workspace, project));
  const { data: runs } = useApi<RunSummary[]>(
    `/runs?workspace=${encodeURIComponent(workspace)}&project=${encodeURIComponent(project)}`,
  );
  const ws = workspaces?.find((w) => w.name === workspace);
  const plants = useMemo(() => (ws ? ws.plants.filter((p) => me!.readable_plants.includes(p)) : []), [ws, me]);

  // One row per question: its newest run (replays and reruns are reached from the run page).
  const questions = useMemo(() => {
    const seen = new Map<string, RunSummary>();
    for (const r of runs ?? []) {
      const key = r.question_id ?? r.run_id;
      const prev = seen.get(key);
      if (!prev || (r.created_at ?? "") > (prev.created_at ?? "")) seen.set(key, r);
    }
    return [...seen.values()].sort((a, b) => (b.updated_at ?? b.created_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? ""));
  }, [runs]);

  const ask = `/ask?workspace=${encodeURIComponent(workspace)}&project=${encodeURIComponent(project)}`;

  return (
    <div className="page">
      <header className="page-head">
        <div className="grow">
          <div className="crumb">{workspace}</div>
          <h1>{project}</h1>
          {proj?.exists && (
            <div className="faint small">
              Started by {proj.owner === me!.id ? "you" : proj.owner} {proj.created_at ? `on ${when(proj.created_at)}` : ""}
            </div>
          )}
        </div>
        <Link to={ask} className="button primary">
          <Icon name="plus" /> Ask a question here
        </Link>
      </header>
      <ErrorNote error={error} />
      {!proj && !error && <Loading />}
      {proj && (
        <div className="section split">
          <div className="split-main stack-lg">
            <section className="panel" aria-labelledby="questions">
              <div className="panel-head">
                <h2 id="questions" className="h-panel">
                  Questions
                </h2>
                <span className="muted small">{questions.length ? `${questions.length} so far` : ""}</span>
              </div>
              {runs && questions.length === 0 && (
                <p className="muted pad">
                  No questions yet. <Link to={ask}>Ask the first one</Link>.
                </p>
              )}
              {!runs && <Loading />}
              <ul className="qlist">
                {questions.map((r) => (
                  <li key={r.run_id}>
                    <Link to={`/runs/${r.run_id}`} className="qrow">
                      <span className="qtext">{r.question ?? r.run_id}</span>
                      <span className="qmeta small muted">
                        {r.user_id === me!.id ? "You" : r.user_id} · {relTime(r.updated_at ?? r.created_at)}
                      </span>
                      <StatusChip status={r.status} />
                    </Link>
                    {!r.entitled && <div className="faint small pad-x">Uses plant data you cannot read: title and status only.</div>}
                  </li>
                ))}
              </ul>
            </section>

            <DataFiles workspace={workspace} project={project} plants={plants} me={me!.id} selected={[]} onSelected={() => {}} pickable={false} />
          </div>

          <aside className="split-side stack">
            <Background
              project={proj}
              onSaved={(p) => {
                setData(p);
                if (!proj.exists) reloadWorkspaces();
              }}
            />
          </aside>
        </div>
      )}
    </div>
  );
}

function Background({ project, onSaved }: { project: Project; onSaved: (p: Project) => void }) {
  const [editing, setEditing] = useState(!project.background);
  const [text, setText] = useState(project.background);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setText(project.background), [project.background]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await api.put<Project>(`${projectPath(project.workspace, project.name)}/background`, { text });
      onSaved(p);
      setEditing(!p.background);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel pad stack-sm" aria-labelledby="bg">
      <h2 id="bg" className="h-panel">
        Background for Alchemist
      </h2>
      <p className="small muted">
        Alchemist reads this before every question in this project. Write what you would tell a colleague: the process and
        product, what normal looks like, recent changes, and what you already suspect or have ruled out.
      </p>
      {editing ? (
        <>
          <textarea
            rows={10}
            value={text}
            maxLength={20000}
            onChange={(e) => setText(e.target.value)}
            placeholder={
              "For example: Line 2 extrudes PP film for packaging. First-pass yield is normally 95 to 97 %. We changed resin supplier in July. Voids usually mean the melt ran too cold. Line speed is fixed by the customer order."
            }
            aria-label="Background for Alchemist"
          />
          <ErrorNote error={error} />
          <div className="row-center">
            <button type="button" className="primary" onClick={save} disabled={busy || text.trim() === project.background}>
              {busy ? "Saving…" : "Save"}
            </button>
            {project.background && (
              <button type="button" className="outline" onClick={() => (setText(project.background), setEditing(false))}>
                Cancel
              </button>
            )}
          </div>
          <p className="faint tiny">Questions already asked keep the background they started with.</p>
        </>
      ) : (
        <>
          <div className="bg-text">{project.background}</div>
          <div className="row-center">
            <button type="button" className="outline small" onClick={() => setEditing(true)}>
              <Icon name="note" /> Edit
            </button>
            {project.background_updated_at && (
              <span className="faint tiny">
                Last changed by {project.background_updated_by} {when(project.background_updated_at)}
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}
