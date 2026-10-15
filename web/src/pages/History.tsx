import { useState } from "react";
import { Link } from "react-router-dom";
import type { RunSummary, Workspace } from "../api";
import { useApi } from "../session";
import { ErrorNote, StatusChip, when } from "../components/bits";

export default function HistoryPage() {
  const [workspace, setWorkspace] = useState("");
  const [project, setProject] = useState("");
  const { data: workspaces } = useApi<Workspace[]>("/workspaces");
  const qs = new URLSearchParams();
  if (workspace) qs.set("workspace", workspace);
  if (project) qs.set("project", project);
  const { data: runs, error } = useApi<RunSummary[]>(`/runs?${qs}`);
  const ws = workspaces?.find((w) => w.name === workspace);

  return (
    <div>
      <h1>Runs</h1>
      <div className="filters">
        <select value={workspace} onChange={(e) => (setWorkspace(e.target.value), setProject(""))}>
          <option value="">All workspaces</option>
          {workspaces?.map((w) => (
            <option key={w.name}>{w.name}</option>
          ))}
        </select>
        <select value={project} onChange={(e) => setProject(e.target.value)} disabled={!ws}>
          <option value="">All projects</option>
          {ws?.projects.map((p) => (
            <option key={p.name}>{p.name}</option>
          ))}
        </select>
      </div>
      <ErrorNote error={error} />
      {runs && runs.length === 0 && <p className="muted">No runs yet. Ask a question to start one.</p>}
      {runs && runs.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Question</th>
              <th>Kind</th>
              <th>Status</th>
              <th>Workspace / project</th>
              <th>Asked by</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.run_id} className={r.entitled ? "" : "restricted"}>
                <td>
                  <Link to={`/runs/${r.run_id}`}>{r.question ?? r.run_id}</Link>
                  {!r.entitled && (
                    <div className="muted small">Uses plants you are not entitled to; you can see its title and status only.</div>
                  )}
                </td>
                <td>{r.mode === "live" ? "investigation" : r.mode}</td>
                <td>
                  <StatusChip status={r.status} />
                </td>
                <td>
                  {r.workspace} / {r.project}
                </td>
                <td>{r.user_id}</td>
                <td className="nowrap">{when(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
