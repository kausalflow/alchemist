import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type RunSummary } from "../api";
import { useSession } from "../session";
import { useWorkspace } from "../workspace";
import { ErrorNote, StatusChip, TopBar, day } from "../components/bits";
import Icon from "../components/icons";

const DEMO = {
  question: "Why was quality low on Line 2 from 3 to 9 August 2026?",
  line: "L2",
  plants: ["P01"],
  from: "2026-08-03",
  to: "2026-08-09",
};

export default function AskPage() {
  const { me, meta } = useSession();
  const { workspaces, current } = useWorkspace();
  const nav = useNavigate();
  const [workspace, setWorkspace] = useState(current);
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
    if (!workspace && current) setWorkspace(current);
  }, [current, workspace]);

  const ws = workspaces?.find((w) => w.name === workspace);
  const plantChoices = useMemo(() => (ws ? ws.plants.filter((p) => me!.readable_plants.includes(p)) : []), [ws, me]);
  const locked = useMemo(() => (ws ? ws.plants.filter((p) => !me!.readable_plants.includes(p)) : []), [ws, me]);

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

  const rerun = async (id: string) => {
    setError(null);
    try {
      const r = await api.post<RunSummary>(`/runs/${id}:replay`, { mode: "rerun" });
      nav(`/runs/${r.run_id}`);
    } catch (err) {
      setError((err as Error).message);
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

  const scopePlants = plants.length ? plants : plantChoices;

  return (
    <div className="fullpage">
      <TopBar>
        <span className="crumb">
          {workspace || "No workspace"}
          {project && <> / <span className="mono">{project}</span></>}
        </span>
      </TopBar>
      <div className="split wide-split">
        <form onSubmit={submit} className="split-main stack-lg">
          <div>
            <h1 className="display">Ask a question</h1>
            <p className="lead measure">
              Alchemist drafts a plan first: the candidate causes, the data and the method for each. Nothing is read from the data lake until
              you approve it.
            </p>
          </div>

          <label className="field">
            <span className="field-label strong-ish">Your question</span>
            <textarea
              className="big"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={4}
              placeholder="Why was first-pass yield low on Line 2 last week?"
              required
              minLength={5}
            />
          </label>
          {meta?.models.scripted && (
            <div>
              <button type="button" className="link" onClick={fillDemo}>
                Use the demo question
              </button>
            </div>
          )}

          <fieldset className="box">
            <legend>Scope (optional)</legend>
            <div className="grid3">
              <label className="field">
                <span className="field-label">Line</span>
                <input className="mono" value={line} onChange={(e) => setLine(e.target.value)} placeholder="L2" />
              </label>
              <label className="field">
                <span className="field-label">From</span>
                <input className="mono" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
              </label>
              <label className="field">
                <span className="field-label">To</span>
                <input className="mono" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
              </label>
            </div>
            <div className="plant-chips" role="group" aria-label="Plants">
              <span className="field-label">Plants</span>
              {plantChoices.map((p) => (
                <label key={p} className={`pchip ${plants.includes(p) ? "on" : ""}`}>
                  <input
                    type="checkbox"
                    checked={plants.includes(p)}
                    onChange={(e) => setPlants(e.target.checked ? [...plants, p] : plants.filter((x) => x !== p))}
                  />
                  <span className="mono">{p}</span>
                </label>
              ))}
              {locked.map((p) => (
                <span key={p} className="pchip locked">
                  <Icon name="lock" size={14} />
                  <span>
                    <span className="mono">{p}</span> not in your groups
                  </span>
                </span>
              ))}
            </div>
            <p className="faint small">None ticked means every plant you may read in this workspace.</p>
          </fieldset>

          <div className="grid2">
            <label className="field">
              <span className="field-label">Workspace</span>
              <select value={workspace} onChange={(e) => (setWorkspace(e.target.value), setPlants([]))} required>
                {workspaces?.map((w) => (
                  <option key={w.name}>{w.name}</option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Project</span>
              <input
                className="mono"
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

          <ErrorNote error={error} />
          <div className="row-center">
            <button className="primary lg" disabled={busy || !workspace}>
              {busy ? "Starting…" : "Draft a plan"}
            </button>
            <span className="muted small">You can leave once it starts; the plan waits on the board under Needs approval.</span>
          </div>
        </form>

        <aside className="split-side stack">
          <section className="panel pad" aria-labelledby="before">
            <h2 id="before" className="h-panel">
              Asked before
            </h2>
            <p className="muted small">Reuse a sealed answer, or rerun it on current data instead of starting over.</p>
            {similar.length === 0 && <p className="faint small">Questions like yours that you can open show up here as you type.</p>}
            <div className="stack-sm">
              {similar.map((s) => (
                <div key={s.run_id} className="subcard">
                  <div className="strong-ish">{s.question}</div>
                  <div className="small muted">
                    {s.user_id === me!.id ? "You" : s.user_id} · <span className="mono">{s.workspace}/{s.project}</span> · {day(s.updated_at ?? s.created_at)}
                  </div>
                  <div className="row-center">
                    <StatusChip status={s.status} />
                    <Link className="button small" to={`/runs/${s.run_id}`}>
                      Open
                    </Link>
                    {s.sealed && (
                      <button type="button" className="outline small" onClick={() => rerun(s.run_id)}>
                        Rerun on current data
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </section>
          <section className="panel pad" aria-labelledby="sees">
            <h2 id="sees" className="h-panel">
              What the agent can see
            </h2>
            <ul className="bullets small">
              <li>
                Approved data sources in the catalog for{" "}
                {scopePlants.length ? <span className="mono">{scopePlants.join(", ")}</span> : "the plants in scope"}, and the shared catalog
              </li>
              <li>Plant documents and reference facts for those plants</li>
              <li>Columns marked never-to-model stay in the sandbox; pseudonymised ones reach it only as codes</li>
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}
