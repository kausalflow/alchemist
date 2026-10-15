import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, TERMINAL, type RunDetail, type Step } from "../api";
import { useApi } from "../session";
import { Chip, ErrorNote, StatusChip, when } from "../components/bits";
import PlanEditor from "../components/PlanEditor";
import StepList from "../components/StepList";
import { runTabs } from "../runTabs";

export default function RunPage() {
  const { runId } = useParams();
  const { data: run, error, reload } = useApi<RunDetail>(`/runs/${runId}`);
  const [live, setLive] = useState<Step[]>([]);
  const [params, setParams] = useSearchParams();
  const status = run?.run.status;

  // Follow progress while the run is planning or running: steps arrive as server-sent events.
  const source = useRef<EventSource | null>(null);
  useEffect(() => {
    if (!run?.entitled || !status || TERMINAL.has(status) || status === "awaiting_approval") return;
    setLive([]);
    const es = new EventSource(`/api/runs/${runId}/events`);
    source.current = es;
    es.addEventListener("step", (e) => setLive((s) => [...s, JSON.parse((e as MessageEvent).data)]));
    es.addEventListener("status", (e) => {
      if (JSON.parse((e as MessageEvent).data).status !== status) reload();
    });
    es.addEventListener("end", () => {
      es.close();
      reload();
    });
    es.onerror = () => {
      es.close();
      setTimeout(reload, 2000);
    };
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, status, run?.entitled]);

  if (error) return <ErrorNote error={error} />;
  if (!run) return <p className="muted">Loading…</p>;
  if (!run.entitled)
    return (
      <div className="narrow">
        <h1>{run.run.question ?? run.run.run_id}</h1>
        <p>
          <StatusChip status={run.run.status} /> asked by {run.run.user_id} in {run.run.workspace}/{run.run.project}
        </p>
        <p className="card">This run read data from plants you are not entitled to, so only its title, owner and status are shown.</p>
      </div>
    );

  const r = run.run;
  const tabs = runTabs.filter((t) => t.show(run));
  const tab = tabs.find((t) => t.id === params.get("tab")) ?? tabs[0];

  const act = (action: "stop" | "pause" | "resume") => async () => {
    await api.post(`/runs/${r.run_id}:${action}`);
    reload();
  };
  const stop = act("stop");

  return (
    <div>
      <div className="runhead">
        <h1>{run.question?.text}</h1>
        <div className="meta">
          <StatusChip status={r.status} />
          {r.mode !== "live" && (
            <Chip tone="info">
              {r.mode} of <Link to={`/runs/${r.parent_run}`}>{r.parent_run?.slice(-8)}</Link>
            </Chip>
          )}
          {r.sealed && <Chip tone="ok">sealed</Chip>}
          <span className="muted small">
            asked by {run.question?.asker} · {r.workspace}/{r.project} · plants {r.plants?.join(", ")} · run{" "}
            <code>{r.run_id}</code> · {when(r.created_at)}
          </span>
        </div>
      </div>

      {r.status === "failed" && run.failure && <p className="error">This run failed: {run.failure.error}</p>}
      {(r.status === "cancelled" || r.status === "expired") && run.closed && (
        <p className="muted">
          {r.status === "cancelled" ? "Cancelled" : "Expired"} {when(run.closed.at)}: {run.closed.reason}
        </p>
      )}

      {status === "planning" && (
        <section className="card">
          <h2>Planning</h2>
          <p className="muted">Searching the catalog and plant documents to propose candidate causes. Nothing is read from the data lake yet.</p>
          <StepList steps={live} live />
          {run.permissions?.stop && (
            <button className="danger" onClick={stop}>
              Stop
            </button>
          )}
        </section>
      )}

      {status === "awaiting_approval" && run.plan && (
        <>
          <PlanEditor run={run} onApproved={reload} />
          {run.permissions?.stop && (
            <p>
              <button className="danger" onClick={stop} title="Discards the plan; nothing has been read from the data lake">
                Cancel run
              </button>
            </p>
          )}
        </>
      )}

      {status === "running" && (
        <section className="card">
          <div className="spread">
            <h2>Running</h2>
            <div className="actions">
              {run.permissions?.pause && (
                <button onClick={act("pause")} title="Pauses after the current step; nothing is lost and it costs nothing while paused">
                  Pause
                </button>
              )}
              {run.permissions?.stop && (
                <button className="danger" onClick={stop} title="Stops after the current step; findings so far still get a partial, sealed report">
                  Stop
                </button>
              )}
            </div>
          </div>
          <p className="muted">Every query and computation below becomes a notebook cell in the run folder.</p>
          <StepList steps={live.filter((s) => r.mode !== "live" || s.phase === "analysis")} live />
          {r.mode !== "live" && <p className="muted">Re-executing the original notebook cells in a fresh kernel…</p>}
        </section>
      )}

      {status === "paused" && (
        <section className="card">
          <div className="spread">
            <h2>Paused</h2>
            <div className="actions">
              {run.permissions?.resume && <button onClick={act("resume")}>Resume</button>}
              {run.permissions?.stop && (
                <button className="danger" onClick={stop} title="Builds and seals a partial report of the findings so far">
                  Stop
                </button>
              )}
            </div>
          </div>
          <p className="muted">
            {run.closed?.reason?.startsWith("interrupted")
              ? "The analysis stopped unexpectedly. "
              : ""}
            The conversation and notebook are saved after every step. Resume re-runs the notebook on its stored
            snapshots in a fresh kernel and carries on from the last step.
          </p>
          <StepList steps={live.filter((s) => s.phase === "analysis")} />
        </section>
      )}

      {(TERMINAL.has(r.status) || status === "awaiting_approval") && (
        <>
          <nav className="tabs">
            {tabs.map((t) => (
              <button key={t.id} className={t.id === tab.id ? "active" : ""} onClick={() => setParams({ tab: t.id })}>
                {t.label}
              </button>
            ))}
          </nav>
          <div className="tabbody">{tab.render(run, reload)}</div>
        </>
      )}
    </div>
  );
}
