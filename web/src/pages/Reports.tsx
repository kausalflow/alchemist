import { useState } from "react";
import { Link } from "react-router-dom";
import type { RunSummary } from "../api";
import { useApi } from "../session";
import { useWorkspace } from "../workspace";
import { ErrorNote, Loading, StatusChip, shortId, when } from "../components/bits";

/** Every run in a workspace as a table: the long view of the board. */
export default function ReportsPage() {
  const { workspaces, current } = useWorkspace();
  const [workspace, setWorkspace] = useState<string | null>(null); // null: follow the rail's workspace
  const [project, setProject] = useState("");
  const [sealedOnly, setSealedOnly] = useState(false);
  const wsName = workspace ?? current;
  const qs = new URLSearchParams();
  if (wsName) qs.set("workspace", wsName);
  if (project) qs.set("project", project);
  const { data: runs, error } = useApi<RunSummary[]>(`/runs?${qs}`);
  const ws = workspaces?.find((w) => w.name === wsName);
  const shown = (runs ?? []).filter((r) => !sealedOnly || ["completed", "partial", "mismatch"].includes(r.status));

  return (
    <div className="page">
      <header className="page-head">
        <div className="grow">
          <div className="crumb">{wsName || "All workspaces"}</div>
          <h1>Reports</h1>
        </div>
      </header>
      <div className="section">
        <div className="filters">
          <label>
            <span className="sr-only">Workspace</span>
            <select value={wsName} onChange={(e) => (setWorkspace(e.target.value), setProject(""))}>
              <option value="">All workspaces</option>
              {workspaces?.map((w) => (
                <option key={w.name}>{w.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">Project</span>
            <select value={project} onChange={(e) => setProject(e.target.value)} disabled={!ws}>
              <option value="">All projects</option>
              {ws?.projects.map((p) => (
                <option key={p.name}>{p.name}</option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="checkbox" checked={sealedOnly} onChange={(e) => setSealedOnly(e.target.checked)} /> Sealed reports only
          </label>
        </div>
        <ErrorNote error={error} />
        {!runs && !error && <Loading />}
        {runs && shown.length === 0 && (
          <p className="muted">
            No runs here yet. <Link to="/ask">Ask a question</Link> to start one.
          </p>
        )}
        {shown.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Question</th>
                  <th scope="col">Run</th>
                  <th scope="col">Kind</th>
                  <th scope="col">Status</th>
                  <th scope="col">Workspace / project</th>
                  <th scope="col">Asked by</th>
                  <th scope="col">Started</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.run_id} className={r.entitled ? "" : "restricted"}>
                    <td>
                      <Link to={`/runs/${r.run_id}`}>{r.question ?? r.run_id}</Link>
                      {!r.entitled && <div className="faint small">Uses plant data you cannot read: title and status only.</div>}
                    </td>
                    <td className="mono small nowrap">{shortId(r.run_id)}</td>
                    <td>{r.mode === "live" ? "investigation" : r.mode}</td>
                    <td>
                      <StatusChip status={r.status} />
                    </td>
                    <td className="mono small">
                      {r.workspace} / {r.project}
                    </td>
                    <td>{r.user_id}</td>
                    <td className="nowrap small">{when(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
