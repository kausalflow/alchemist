import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type RunSummary, type Workspace } from "../api";
import { useApi, useSession } from "../session";
import { StatusChip } from "../components/bits";

const DEMO = {
  question: "Why was quality low on Line 2 from 3 to 9 August 2026?",
  line: "L2",
  plants: ["P01"],
  from: "2026-08-03",
  to: "2026-08-09",
};

export default function AskPage() {
  const { me, meta } = useSession();
  const nav = useNavigate();
  const { data: workspaces } = useApi<Workspace[]>("/workspaces");
  const [workspace, setWorkspace] = useState("");
  const [project, setProject] = useState("");
  const [question, setQuestion] = useState("");
  const [line, setLine] = useState("");
  const [plants, setPlants] = useState<string[]>([]);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [similar, setSimilar] = useState<RunSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (workspaces?.length && !workspace) setWorkspace(workspaces[0].name);
  }, [workspaces, workspace]);

  const ws = workspaces?.find((w) => w.name === workspace);
  const plantChoices = useMemo(
    () => (ws ? ws.plants.filter((p) => me!.readable_plants.includes(p)) : []),
    [ws, me],
  );

  useEffect(() => {
    if (question.trim().length < 12) {
      setSimilar([]);
      return;
    }
    const t = setTimeout(() => {
      api.get<RunSummary[]>(`/questions/similar?q=${encodeURIComponent(question)}`).then(setSimilar).catch(() => {});
    }, 400);
    return () => clearTimeout(t);
  }, [question]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const scope: Record<string, unknown> = {};
    if (line) scope.line = line;
    if (plants.length) scope.plants = plants;
    if (from && to) scope.period = [from, to];
    try {
      const run = await api.post<RunSummary>("/questions", { workspace, project, question, scope });
      nav(`/runs/${run.run_id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  const fillDemo = () => {
    setQuestion(DEMO.question);
    setLine(DEMO.line);
    setPlants(DEMO.plants.filter((p) => plantChoices.includes(p)));
    setFrom(DEMO.from);
    setTo(DEMO.to);
    if (!project) setProject("line2-yield");
  };

  return (
    <div className="narrow">
      <h1>Ask a question</h1>
      <p className="muted">
        Alchemist proposes a plan of candidate causes, the data and the method for each. You review and approve it before
        any data is read; then it runs the analysis, has it reviewed, and writes a sealed report you can replay.
      </p>
      <form onSubmit={submit} className="card form">
        <label>
          Question
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={3}
            placeholder="Why was first-pass yield low on Line 2 last week?"
            required
            minLength={5}
          />
        </label>
        {meta?.models.scripted && (
          <button type="button" className="link small" onClick={fillDemo}>
            Use the demo question
          </button>
        )}
        <div className="row">
          <label>
            Workspace
            <select value={workspace} onChange={(e) => setWorkspace(e.target.value)} required>
              {workspaces?.map((w) => (
                <option key={w.name}>{w.name}</option>
              ))}
            </select>
          </label>
          <label>
            Project
            <input
              list="projects"
              value={project}
              onChange={(e) => setProject(e.target.value)}
              placeholder="line2-yield"
              pattern="[A-Za-z0-9][A-Za-z0-9_.\-]*"
              title="letters, digits, dot, dash and underscore"
              required
            />
            <datalist id="projects">
              {ws?.projects.map((p) => (
                <option key={p.name} value={p.name} />
              ))}
            </datalist>
          </label>
        </div>
        <fieldset>
          <legend>Scope (optional)</legend>
          <div className="row">
            <label>
              Line
              <input value={line} onChange={(e) => setLine(e.target.value)} placeholder="L2" />
            </label>
            <label>
              From
              <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
            </label>
            <label>
              To
              <input type="date" value={to} onChange={(e) => setTo(e.target.value)} />
            </label>
          </div>
          <div className="plants">
            Plants
            {plantChoices.map((p) => (
              <label key={p} className="check">
                <input
                  type="checkbox"
                  checked={plants.includes(p)}
                  onChange={(e) => setPlants(e.target.checked ? [...plants, p] : plants.filter((x) => x !== p))}
                />
                {p}
              </label>
            ))}
            <span className="muted small">none ticked means every plant you may read in this workspace</span>
          </div>
        </fieldset>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy || !workspace}>
          {busy ? "Starting…" : "Plan the investigation"}
        </button>
      </form>
      {similar.length > 0 && (
        <section>
          <h3>Similar past questions</h3>
          <ul className="plain">
            {similar.map((s) => (
              <li key={s.run_id}>
                <Link to={`/runs/${s.run_id}`}>{s.question}</Link> <StatusChip status={s.status} />{" "}
                <span className="muted small">
                  {s.workspace}/{s.project} · {s.user_id}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
