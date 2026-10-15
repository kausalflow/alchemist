// The run page's tabs, as a registry. A module adds a tab by pushing to `runTabs` (or calling `registerRunTab`)
// before the app renders; nothing else in the page needs to change.

import type { ReactNode } from "react";
import { TERMINAL, type RunDetail } from "./api";
import { AnswerTab, DataTab, KnowledgeTab, NotebookTab, PlanTab, ProvenanceTab, ReportTab, ReviewTab, StepsTab } from "./components/tabs";

export interface RunTab {
  id: string;
  label: string;
  show: (run: RunDetail) => boolean;
  render: (run: RunDetail, reload: () => void) => ReactNode;
}

const done = (r: RunDetail) => TERMINAL.has(r.run.status);

export const runTabs: RunTab[] = [
  { id: "answer", label: "Answer", show: (r) => done(r) && r.run.mode === "live", render: (r, reload) => <AnswerTab run={r} reload={reload} /> },
  { id: "replay", label: "Replay result", show: (r) => done(r) && r.run.mode !== "live", render: (r) => <AnswerTab run={r} reload={() => {}} /> },
  { id: "plan", label: "Plan", show: (r) => !!r.plan && r.run.status !== "awaiting_approval", render: (r) => <PlanTab run={r} /> },
  { id: "data", label: "Data used", show: done, render: (r) => <DataTab run={r} /> },
  { id: "notebook", label: "Notebook", show: (r) => !!r.has_notebook, render: (r) => <NotebookTab run={r} /> },
  { id: "knowledge", label: "Knowledge", show: (r) => !!r.knowledge?.citations.length || !!r.knowledge?.structured.length, render: (r) => <KnowledgeTab run={r} /> },
  { id: "review", label: "Review", show: (r) => !!r.review, render: (r) => <ReviewTab run={r} /> },
  { id: "steps", label: "Agent steps", show: () => true, render: (r) => <StepsTab run={r} /> },
  { id: "report", label: "Report", show: (r) => !!r.has_report, render: (r) => <ReportTab run={r} /> },
  { id: "provenance", label: "Provenance", show: (r) => !!r.manifest, render: (r, reload) => <ProvenanceTab run={r} reload={reload} /> },
];

export function registerRunTab(tab: RunTab, before?: string) {
  const i = before ? runTabs.findIndex((t) => t.id === before) : -1;
  if (i >= 0) runTabs.splice(i, 0, tab);
  else runTabs.push(tab);
}
