import { Link } from "react-router-dom";
import type { RunSummary } from "../api";
import { useApi, useSession } from "../session";
import { ErrorNote, Loading } from "../components/bits";
import { WaitingCards, waitingOnMe } from "../components/Waiting";

/** Everything waiting for the signed-in user, across their workspaces. */
export default function WaitingPage() {
  const { me } = useSession();
  const { data: runs, error } = useApi<RunSummary[]>("/runs");
  const waiting = runs ? waitingOnMe(runs, me!) : [];
  return (
    <div className="page">
      <header className="page-head">
        <div className="grow">
          <div className="crumb">All your workspaces</div>
          <h1>Waiting on you</h1>
        </div>
      </header>
      <div className="section">
        <p className="muted measure">
          Plans you asked for that need your approval before any data is read, and runs you paused. Nothing moves until you act.
        </p>
        <ErrorNote error={error} />
        {!runs && !error && <Loading />}
        {runs && waiting.length === 0 && (
          <p className="muted">
            Nothing is waiting on you. <Link to="/">Back to the board</Link>
          </p>
        )}
        {waiting.length > 0 && <WaitingCards runs={waiting} />}
      </div>
    </div>
  );
}
