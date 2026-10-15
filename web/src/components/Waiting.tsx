import { Link } from "react-router-dom";
import type { Me, RunSummary } from "../api";
import { relTime, shortId } from "./bits";

/** Runs that cannot move until the signed-in user acts: their plans to approve and their paused runs.
 * (Sealed runs without a verdict would belong here too, but /runs does not say which have one.) */
export function waitingOnMe(runs: RunSummary[], me: Me): RunSummary[] {
  return runs.filter((r) => r.user_id === me.id && r.entitled && (r.status === "awaiting_approval" || r.status === "paused"));
}

export function WaitingCards({ runs }: { runs: RunSummary[] }) {
  return (
    <div className="wait-grid">
      {runs.map((r) => (
        <Link key={r.run_id} to={`/runs/${r.run_id}`} className={`wait-card ${r.status === "awaiting_approval" ? "hot" : ""}`}>
          <div className="wait-top small">
            <span className="warn-text strong">{r.status === "awaiting_approval" ? "Approve plan" : "Paused"}</span>
            <span className="mono faint">{shortId(r.run_id)}</span>
          </div>
          <div className="strong-ish">{r.question ?? r.run_id}</div>
          <div className="small muted">
            {r.status === "awaiting_approval"
              ? `Plan ready ${relTime(r.updated_at ?? r.created_at)} · ${r.workspace}/${r.project}`
              : "Resume, or stop and get a partial report"}
          </div>
        </Link>
      ))}
    </div>
  );
}
