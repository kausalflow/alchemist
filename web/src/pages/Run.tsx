import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, fileUrl, CLOSED, SEALED, TERMINAL, type RunDetail, type Step } from "../api";
import { useApi, useSession } from "../session";
import { ErrorNote, Loading, StatusChip, TopBar, shortId, when } from "../components/bits";
import Icon from "../components/icons";
import PlanEditor from "../components/PlanEditor";
import StepList, { isReviewing } from "../components/StepList";
import Stepper from "../components/Stepper";
import { SteerPanel } from "../components/Steer";
import { QuestionRuns, ReproducePanel, VerdictPanel, useReplay } from "../components/tabs";
import { runTabs } from "../runTabs";

export default function RunPage() {
  const { runId } = useParams();
  const nav = useNavigate();
  const { data: run, error, reload } = useApi<RunDetail>(`/runs/${runId}`);
  const [live, setLive] = useState<Step[]>([]);
  const status = run?.run.status;

  // Follow progress while the run is planning, running or paused: steps arrive as server-sent events.
  const source = useRef<EventSource | null>(null);
  useEffect(() => {
    if (!run?.entitled || !status || TERMINAL.has(status) || status === "awaiting_approval") return;
    setLive([]);
    const es = new EventSource(`/api/runs/${runId}/events`);
    source.current = es;
    es.addEventListener("step", (e) => {
      const step = JSON.parse((e as MessageEvent).data);
      setLive((s) => [...s, step]);
      if (step.type === "note") reload(); // the agent read a note: show it as read
    });
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

  if (error)
    return (
      <div className="fullpage">
        <TopBar />
        <div className="pad-page">
          <ErrorNote error={error} />
        </div>
      </div>
    );
  if (!run) return <Loading />;

  const r = run.run;
  const crumb = (
    <>
      <span className="crumb">
        {r.workspace} / <span className="mono">{r.project}</span>
      </span>
      <span className="mono faint small">run {r.run_id}</span>
    </>
  );

  if (!run.entitled)
    return (
      <div className="fullpage">
        <TopBar>
          {crumb}
          <span className="push">
            <StatusChip status={r.status} />
          </span>
        </TopBar>
        <div className="pad-page measure stack">
          <h1 className="title">{r.question ?? r.run_id}</h1>
          <p className="muted">
            Asked by {r.user_id} in {r.workspace}/{r.project}.
          </p>
          <div className="callout">
            <Icon name="lock" /> This run uses plant data you cannot read, so only its title, owner and status are shown.
          </div>
        </div>
      </div>
    );

  const analysisSteps = live.filter((s) => r.mode !== "live" || s.phase === "analysis");
  const reviewing = r.status === "running" && isReviewing(analysisSteps);
  const toolCount = analysisSteps.filter((s) => s.type === "tool").length;

  const act = (action: "stop" | "pause" | "resume") => async () => {
    await api.post(`/runs/${r.run_id}:${action}`);
    reload();
  };

  return (
    <div className="fullpage">
      <TopBar>
        {crumb}
        {r.mode !== "live" && r.parent_run && (
          <span className="small muted">
            {r.mode} of <Link to={`/runs/${r.parent_run}`}>run {shortId(r.parent_run)}</Link>
          </span>
        )}
        <span className="push">
          <StatusChip status={r.status}>
            {r.status === "running" && toolCount
              ? `${reviewing ? "In review" : "Running"} · step ${toolCount}`
              : r.status === "awaiting_approval"
                ? "Needs approval"
                : r.sealed && r.status === "completed"
                  ? `Sealed ${when(r.updated_at)}`
                  : undefined}
          </StatusChip>
        </span>
      </TopBar>
      <Stepper run={run} reviewing={reviewing} />

      <div className="pad-page">
        {status === "planning" && (
          <PlanningView run={run} steps={live.filter((s) => s.phase === "planning")} onStop={act("stop")} reload={reload} />
        )}

        {status === "awaiting_approval" && run.plan && <PlanEditor run={run} onApproved={reload} onCancel={act("stop")} />}

        {(status === "running" || status === "paused") && (
          <RunningView run={run} steps={analysisSteps} reviewing={reviewing} act={act} reload={reload} />
        )}

        {status && SEALED.has(status) && <ReportView run={run} reload={reload} />}

        {status && CLOSED.has(status) && <ClosedView run={run} reload={reload} onAsk={() => nav("/ask")} />}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ planning

function PlanningView({ run, steps, onStop, reload }: { run: RunDetail; steps: Step[]; onStop: () => Promise<void>; reload: () => void }) {
  const revising = (run.notes ?? []).some((n) => n.kind === "send_back");
  return (
    <div className="split">
      <div className="split-main stack">
        <h1 className="title">{run.question?.text}</h1>
        <section className="panel" aria-labelledby="planlog">
          <div className="panel-head">
            <h2 id="planlog" className="h-panel">
              {revising ? "Revising the plan" : "Drafting the plan"}
            </h2>
            <span className="muted small">Searching the catalog and plant documents. Nothing is read from the data lake yet.</span>
          </div>
          <StepList steps={steps} live />
        </section>
      </div>
      <aside className="split-side stack">
        <section className="panel pad stack-sm" aria-labelledby="next">
          <h2 id="next" className="h-panel">
            What happens next
          </h2>
          <p className="small muted">
            When the plan is ready it waits under Needs approval. {run.question?.asker} reviews the candidate causes, the data and the methods
            before anything is read. You can leave this page.
          </p>
          {run.permissions?.stop && (
            <>
              <button type="button" className="danger" onClick={onStop}>
                <Icon name="stop" /> Stop drafting
              </button>
              <div className="small muted">The question is cancelled; nothing has been read from the data lake.</div>
            </>
          )}
        </section>
        <SteerPanel run={run} reload={reload} />
      </aside>
    </div>
  );
}

// ------------------------------------------------------------------ running and paused

function RunningView({
  run,
  steps,
  reviewing,
  act,
  reload,
}: {
  run: RunDetail;
  steps: Step[];
  reviewing: boolean;
  act: (a: "stop" | "pause" | "resume") => () => Promise<void>;
  reload: () => void;
}) {
  const { meta } = useSession();
  const r = run.run;
  const paused = r.status === "paused";
  const tools = steps.filter((s) => s.type === "tool").length;
  const tokens = steps.reduce((n, s) => n + (s.usage?.output_tokens ?? 0), 0);
  const found = new Map<string, number>();
  for (const s of steps)
    if (s.tool === "record_finding" && typeof s.input?.hypothesis === "string") found.set(s.input.hypothesis, (found.get(s.input.hypothesis) ?? 0) + 1);

  const limits = meta
    ? [
        { name: "Tool calls", used: tools, max: meta.budgets.max_steps },
        { name: "Output tokens", used: tokens, max: meta.budgets.max_output_tokens },
      ]
    : [];

  return (
    <div className="split3">
      <aside className="side-left stack">
        <h1 className="title-sm">{run.question?.text}</h1>
        {run.plan?.approved_by && (
          <div className="small muted">
            Approved by {run.plan.approved_by} {when(run.plan.approved_at)}
          </div>
        )}
        {run.plan && (
          <section aria-labelledby="prog" className="panel causes">
            <h2 id="prog" className="eyebrow pad-x">
              Causes
            </h2>
            {run.plan.hypotheses.map((h) => {
              const n = found.get(h.id) ?? 0;
              return (
                <div key={h.id} className="cause">
                  <span className="mono muted">{h.id}</span>
                  <div>
                    <div>{h.cause}</div>
                    <div className={`small ${n ? "ok-text" : "faint"}`}>
                      {n ? `${n === 1 ? "Finding" : `${n} findings`} recorded` : "No finding yet"}
                    </div>
                  </div>
                </div>
              );
            })}
          </section>
        )}
      </aside>

      <section aria-labelledby="log" className="panel center-col">
        <div className="panel-head">
          <h2 id="log" className="h-panel">
            {paused ? "What it did before pausing" : "What it is doing"}
          </h2>
          <span className="muted small">Every query and computation becomes a notebook cell in the run folder.</span>
          {run.has_notebook && (
            <a className="push small" href={fileUrl(r.run_id, "notebook.ipynb", true)}>
              Download the notebook so far
            </a>
          )}
        </div>
        <StepList
          steps={steps}
          live={!paused}
          liveText={
            r.mode !== "live" ? "Re-executing the original notebook cells in a fresh kernel…" : reviewing ? "A second model is reviewing the findings…" : undefined
          }
        />
      </section>

      <aside className="side-right stack">
        <section aria-labelledby="ctl" className="panel pad stack-sm">
          <h2 id="ctl" className="h-panel">
            Controls
          </h2>
          {paused && (
            <p className="small muted">
              {run.closed?.reason?.startsWith("interrupted") ? "The analysis stopped unexpectedly. " : ""}
              The conversation and notebook are saved after every step.
            </p>
          )}
          {run.permissions?.pause && (
            <>
              <button type="button" className="outline" onClick={act("pause")}>
                <Icon name="pause" /> Pause after this step
              </button>
              <div className="small muted">Nothing is lost and nothing is spent while paused.</div>
            </>
          )}
          {run.permissions?.resume && (
            <>
              <button type="button" className="primary" onClick={act("resume")}>
                <Icon name="play" /> Resume
              </button>
              <div className="small muted">Re-runs the notebook on its stored snapshots in a fresh kernel, then carries on from the last step.</div>
            </>
          )}
          {run.permissions?.stop && (
            <>
              <button type="button" className="danger" onClick={act("stop")}>
                <Icon name="stop" /> Stop and write a partial report
              </button>
              <div className="small muted">Stops after the current step; the findings so far are reviewed and sealed.</div>
            </>
          )}
          {!run.permissions?.pause && !run.permissions?.resume && !run.permissions?.stop && (
            <p className="small muted">Only {r.user_id} or an admin can pause, stop or steer this run.</p>
          )}
        </section>
        <SteerPanel run={run} reload={reload} />
        {limits.length > 0 && (
          <section aria-labelledby="lim" className="panel pad stack-sm">
            <h2 id="lim" className="h-panel">
              Run limits
            </h2>
            {limits.map((l) => {
              const pct = Math.min(100, l.max ? (l.used / l.max) * 100 : 0);
              return (
                <div key={l.name} className="meter">
                  <div className="spread small">
                    <span className="muted">{l.name}</span>
                    <span className="mono">
                      {l.used.toLocaleString()} / {l.max.toLocaleString()}
                    </span>
                  </div>
                  <div
                    className="meter-track"
                    role="meter"
                    aria-label={l.name}
                    aria-valuemin={0}
                    aria-valuemax={l.max}
                    aria-valuenow={l.used}
                  >
                    <div className="meter-fill" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
            <p className="faint tiny">Limits per run step, set in the deployment's configuration. Counted from the steps shown here.</p>
          </section>
        )}
      </aside>
    </div>
  );
}

// ------------------------------------------------------------------ sealed

function RunTabs({ run, reload }: { run: RunDetail; reload: () => void }) {
  const [params, setParams] = useSearchParams();
  const tabs = runTabs.filter((t) => t.show(run));
  if (!tabs.length) return null;
  const tab = tabs.find((t) => t.id === params.get("tab")) ?? tabs[0];
  return (
    <>
      <div role="tablist" aria-label="Report sections" className="tabs">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={t.id === tab.id}
            aria-controls="tabpanel"
            className={t.id === tab.id ? "active" : ""}
            onClick={() => setParams({ tab: t.id })}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="tabpanel" aria-labelledby={`tab-${tab.id}`} className="tabbody">
        {tab.render(run, reload)}
      </div>
    </>
  );
}

function ReportView({ run, reload }: { run: RunDetail; reload: () => void }) {
  const r = run.run;
  const { start, busy, error } = useReplay(run, reload);
  const supported = (run.findings ?? []).filter((f) => f.verdict === "supported");
  return (
    <div className="split">
      <div className="split-main stack">
        <div className="title-row">
          <h1 className="title grow">{run.question?.text}</h1>
          <div className="row-center">
            {run.has_report && (
              <a className="button" href={fileUrl(r.run_id, "report/report.html", true)}>
                <Icon name="download" /> Download report
              </a>
            )}
            {run.permissions?.replay && (
              <>
                <button type="button" className="outline" onClick={() => start("rerun")} disabled={busy}>
                  Rerun on current data
                </button>
                <button type="button" className="primary" onClick={() => start("replay")} disabled={busy}>
                  <Icon name="replay" /> Replay
                </button>
              </>
            )}
          </div>
        </div>
        <ErrorNote error={error} />
        {r.status === "partial" && (
          <div className="callout warn small">Partial report: the run was stopped before every cause was tested. The findings so far were reviewed and sealed.</div>
        )}
        {r.status === "mismatch" && (
          <div className="callout bad small">This {r.mode} did not reproduce the original values. See the comparison under Replay result.</div>
        )}

        {run.conclusion_html ? (
          <section aria-labelledby="ans" className="answer">
            <h2 id="ans" className="eyebrow ok-text">
              Answer
            </h2>
            <article className="answer-body" dangerouslySetInnerHTML={{ __html: run.conclusion_html }} />
            {supported.length > 0 && (
              <div className="chips">
                {supported.map((f) => (
                  <span key={f.id} className={`tag ${f.confidence === "high" ? "ok" : "warn"}`}>
                    {f.hypothesis} · {f.confidence} confidence · {f.id}
                  </span>
                ))}
              </div>
            )}
          </section>
        ) : r.mode !== "live" ? null : (
          <p className="muted">No conclusion was written for this run.</p>
        )}

        <RunTabs run={run} reload={reload} />
      </div>
      <aside className="split-side stack">
        <ReproducePanel run={run} />
        <QuestionRuns run={run} />
        {r.mode === "live" && (run.permissions?.verdict || !!run.verdicts?.length) && <VerdictPanel run={run} reload={reload} />}
      </aside>
    </div>
  );
}

// ------------------------------------------------------------------ failed, cancelled, expired

function ClosedView({ run, reload, onAsk }: { run: RunDetail; reload: () => void; onAsk: () => void }) {
  const r = run.run;
  return (
    <div className="stack">
      <h1 className="title">{run.question?.text ?? r.question}</h1>
      {r.status === "failed" && (
        <div className="callout bad">
          <strong>This run failed.</strong> {run.failure?.error}
        </div>
      )}
      {(r.status === "cancelled" || r.status === "expired") && (
        <div className="callout">
          {r.status === "cancelled" ? "Cancelled" : "The plan expired"}
          {run.closed && (
            <>
              {" "}
              {when(run.closed.at)}
              {run.closed.actor && run.closed.actor !== "system" && ` by ${run.closed.actor}`}: {run.closed.reason}
            </>
          )}
          .{" "}
          <button type="button" className="link" onClick={onAsk}>
            Ask again
          </button>
        </div>
      )}
      <RunTabs run={run} reload={reload} />
    </div>
  );
}
