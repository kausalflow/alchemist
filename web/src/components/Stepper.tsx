import type { RunDetail } from "../api";
import Icon from "./icons";

/** Where a run is: Asked, Plan drafted, Your approval, Analysis, Review, Sealed. */
export function stageOf(run: RunDetail, reviewing: boolean): { current: number; tone: "warn" | "info" | "plan" | "bad" | "done" } {
  const s = run.run.status;
  const approved = !!run.plan?.approved_at;
  switch (s) {
    case "planning":
      return { current: 1, tone: "plan" };
    case "awaiting_approval":
      return { current: 2, tone: "warn" };
    case "running":
      return { current: reviewing ? 4 : 3, tone: reviewing ? "plan" : "info" };
    case "paused":
      return { current: 3, tone: "warn" };
    case "completed":
    case "partial":
    case "mismatch":
      return { current: 6, tone: "done" };
    case "expired":
      return { current: 2, tone: "bad" };
    default: // failed, cancelled
      return { current: approved || run.run.mode !== "live" ? 3 : run.plan ? 2 : 1, tone: "bad" };
  }
}

export default function Stepper({ run, reviewing = false }: { run: RunDetail; reviewing?: boolean }) {
  const { current, tone } = stageOf(run, reviewing);
  const labels = ["Asked", "Plan drafted", current > 2 ? "Approved" : "Your approval", "Analysis", "Review", "Sealed"];
  return (
    <ol className="stepper" aria-label="Run stages">
      {labels.map((label, i) => {
        const state = i < current ? "st-done" : i === current ? `st-now ${tone}` : "st-todo";
        return (
          <li key={label} className={state} aria-current={i === current ? "step" : undefined}>
            <span className="bar" aria-hidden="true" />
            <span className="lbl">
              {i < current && <Icon name="check" size={12} />}
              {label}
              {i === current && <span className="sr-only"> (current stage)</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
