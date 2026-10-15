import { useState, type ReactNode } from "react";

const STATUS_LABEL: Record<string, string> = {
  planning: "planning",
  awaiting_approval: "plan waiting for approval",
  running: "running",
  completed: "completed",
  partial: "partial",
  failed: "failed",
  mismatch: "replay mismatch",
};

export function StatusChip({ status }: { status: string }) {
  return <span className={`chip status-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function Chip({ children, tone }: { children: ReactNode; tone?: string }) {
  return <span className={`chip ${tone ?? ""}`}>{children}</span>;
}

export function Code({ children, lang }: { children: string; lang?: string }) {
  return (
    <pre className={`code ${lang ?? ""}`}>
      <code>{children}</code>
    </pre>
  );
}

export function Collapsible({ title, children, open = false }: { title: ReactNode; children: ReactNode; open?: boolean }) {
  const [isOpen, setOpen] = useState(open);
  return (
    <div className={`collapsible ${isOpen ? "open" : ""}`}>
      <button className="collapsible-head" onClick={() => setOpen(!isOpen)} aria-expanded={isOpen}>
        <span className="caret">{isOpen ? "▾" : "▸"}</span> {title}
      </button>
      {isOpen && <div className="collapsible-body">{children}</div>}
    </div>
  );
}

export function fmtValue(v: unknown, unit?: string) {
  let text: string;
  if (typeof v === "number") text = Math.abs(v) >= 1 || v === 0 ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : v.toPrecision(3);
  else text = String(v);
  return unit ? `${text} ${unit}` : text;
}

export function when(iso?: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <p className="error">{error}</p> : null;
}
