// The sealed run's tabs, as a registry. A module adds a tab by pushing to `runTabs` (or calling `registerRunTab`)
// before the app renders; nothing else in the page needs to change.

import type { ReactNode } from "react";
import { TERMINAL, type RunDetail } from "./api";
import { DataTab, FindingsTab, MethodsTab, NotebookTab, ProvenanceTab, ReportTab, StepsTab } from "./components/tabs";

export interface RunTab {
  id: string;
  label: string;
  show: (run: RunDetail) => boolean;
  render: (run: RunDetail, reload: () => void) => ReactNode;
}

const done = (r: RunDetail) => TERMINAL.has(r.run.status);

// Knowledge is folded into "Methods and plan" and the reviewer's notes into "Findings"; provenance also has a
// summary in the page's side panel.
export const runTabs: RunTab[] = [
  { id: "findings", label: "Findings", show: (r) => done(r) && r.run.mode === "live", render: (r) => <FindingsTab run={r} /> },
  { id: "replay", label: "Replay result", show: (r) => done(r) && r.run.mode !== "live", render: (r) => <FindingsTab run={r} /> },
  { id: "data", label: "Data", show: (r) => done(r) && (!!r.datasets?.length || !!r.queries?.length), render: (r) => <DataTab run={r} /> },
  {
    id: "plan",
    label: "Methods and plan",
    show: (r) => !!r.plan || !!r.knowledge?.citations.length || !!r.knowledge?.structured.length,
    render: (r) => <MethodsTab run={r} />,
  },
  { id: "notebook", label: "Notebook", show: (r) => !!r.has_notebook, render: (r) => <NotebookTab run={r} /> },
  { id: "report", label: "Full report", show: (r) => !!r.has_report, render: (r) => <ReportTab run={r} /> },
  { id: "steps", label: "Agent steps", show: () => true, render: (r) => <StepsTab run={r} /> },
  { id: "provenance", label: "Provenance", show: (r) => !!r.manifest, render: (r, reload) => <ProvenanceTab run={r} reload={reload} /> },
];

export function registerRunTab(tab: RunTab, before?: string) {
  const i = before ? runTabs.findIndex((t) => t.id === before) : -1;
  if (i >= 0) runTabs.splice(i, 0, tab);
  else runTabs.push(tab);
}
