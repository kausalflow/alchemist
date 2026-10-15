import { useState } from "react";
import { api, TERMINAL, type RunDetail, type RunNote } from "../api";
import { ErrorNote, when } from "./bits";
import Icon from "./icons";

/** Where a note stands: read by the agent (and at which step), waiting for its next step, or never read. */
export function noteState(run: RunDetail, n: RunNote): { text: string; waiting: boolean } {
  const d = n.delivered;
  if (d) {
    if (d.phase === "planning") return { text: n.kind === "send_back" ? "Read while revising the plan" : "Read while planning", waiting: false };
    return { text: d.step ? `Read after step ${d.step}` : "Read when the analysis started", waiting: false };
  }
  const s = run.run.status;
  if (TERMINAL.has(s)) return { text: "Not read: the run ended first", waiting: false };
  if (s === "paused") return { text: "Read when the run resumes", waiting: true };
  if (s === "awaiting_approval") return { text: "Read when the analysis starts", waiting: true };
  return { text: "Read before its next step", waiting: true };
}

export function NoteList({ run, notes, newestFirst = false }: { run: RunDetail; notes: RunNote[]; newestFirst?: boolean }) {
  return (
    <ol className="notes">
      {(newestFirst ? [...notes].reverse() : notes).map((n) => {
        const st = noteState(run, n);
        return (
          <li key={n.id} className={st.waiting ? "waiting" : ""}>
            <span className="small">
              <span className="strong">{n.author}</span> <span className="faint">{when(n.at)}</span>
              {n.kind === "send_back" && <span className="faint"> · sent the plan back</span>}
            </span>
            <span className="note-text">{n.text}</span>
            <span className={`tiny ${st.waiting ? "warn-text" : "faint"}`}>{st.text}</span>
          </li>
        );
      })}
    </ol>
  );
}

/** A box for leaving the agent a note while it plans or analyses, or while the run is paused. */
export function SteerPanel({ run, reload }: { run: RunDetail; reload: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const notes = run.notes ?? [];
  const status = run.run.status;
  if (!run.permissions?.note && !notes.length) return null;

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/runs/${run.run.run_id}/notes`, { text });
      setText("");
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="steer" className="panel pad stack-sm">
      <h2 id="steer" className="h-panel">
        Steer the agent
      </h2>
      {run.permissions?.note && (
        <>
          <label className="field">
            <span className="field-label">Note for the agent</span>
            <textarea
              rows={3}
              value={text}
              maxLength={4000}
              placeholder="For example: the melt-flow sensor drifted in March, exclude those readings."
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <ErrorNote error={error} />
          <button type="button" className="outline" onClick={send} disabled={busy || !text.trim()}>
            <Icon name="note" /> {busy ? "Sending…" : "Send note"}
          </button>
          <div className="small muted">
            {status === "paused"
              ? "The agent reads it when the run resumes."
              : status === "planning"
                ? "The agent reads it before its next step and drafts the plan with it."
                : "The agent reads it before its next step and takes it into account from there."}{" "}
            Every note is kept with the run and shown in its report.
          </div>
        </>
      )}
      {notes.length > 0 && <NoteList run={run} notes={notes} newestFirst />}
    </section>
  );
}

/** The sealed run's notes, as a tab. */
export function NotesTab({ run }: { run: RunDetail }) {
  return (
    <div className="stack-sm">
      <p className="muted small">
        Notes people left for the agent while it worked, and when it read each one. Notes steer the agent; they are not evidence. The
        notebook and the agent steps show what it did after each note.
      </p>
      <NoteList run={run} notes={run.notes ?? []} />
    </div>
  );
}
