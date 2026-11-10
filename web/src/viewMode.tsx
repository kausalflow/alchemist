// Simple or detailed view, remembered in this browser. Simple hides code, SQL, tool names and the technical panels,
// for people who want the story, figures, tables and findings; detailed shows everything as before.

import { useSyncExternalStore } from "react";

export type ViewMode = "simple" | "detailed";

const KEY = "alchemist.view";
const listeners = new Set<() => void>();

function read(): ViewMode {
  try {
    return localStorage.getItem(KEY) === "simple" ? "simple" : "detailed";
  } catch {
    return "detailed";
  }
}

let current: ViewMode = read();

export function setViewMode(mode: ViewMode) {
  current = mode;
  try {
    localStorage.setItem(KEY, mode);
  } catch {
    /* storage blocked: the choice lasts for this page only */
  }
  listeners.forEach((l) => l());
}

export function useViewMode(): ViewMode {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => current,
  );
}

/** True in the simple view. */
export const useSimple = () => useViewMode() === "simple";

export function ViewToggle() {
  const mode = useViewMode();
  return (
    <div className="seg view-toggle" role="group" aria-label="How much detail to show">
      <button type="button" aria-pressed={mode === "simple"} onClick={() => setViewMode("simple")} title="Hide code and technical details">
        Simple
      </button>
      <button type="button" aria-pressed={mode === "detailed"} onClick={() => setViewMode("detailed")} title="Show code, SQL and every step">
        Detailed
      </button>
    </div>
  );
}
