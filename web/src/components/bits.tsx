import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import Icon from "./icons";

const STATUS: Record<string, { label: string; tone: string }> = {
  planning: { label: "Drafting plan", tone: "plan" },
  awaiting_approval: { label: "Needs approval", tone: "warn" },
  running: { label: "Running", tone: "info" },
  paused: { label: "Paused", tone: "warn" },
  completed: { label: "Sealed", tone: "ok" },
  partial: { label: "Partial, sealed", tone: "ok" },
  mismatch: { label: "Replay mismatch", tone: "bad" },
  failed: { label: "Failed", tone: "bad" },
  cancelled: { label: "Cancelled", tone: "" },
  expired: { label: "Plan expired", tone: "" },
};

export const statusLabel = (s: string) => STATUS[s]?.label ?? s;
export const statusTone = (s: string) => STATUS[s]?.tone ?? "";

/** A run's status as a small pill with a dot. */
export function StatusChip({ status, children }: { status: string; children?: ReactNode }) {
  return (
    <span className={`pill ${statusTone(status)}`}>
      <span className="dot" aria-hidden="true" />
      {children ?? statusLabel(status)}
    </span>
  );
}

export function Chip({ children, tone, mono }: { children: ReactNode; tone?: string; mono?: boolean }) {
  return <span className={`chip ${tone ?? ""} ${mono ? "mono" : ""}`}>{children}</span>;
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
      <button type="button" className="collapsible-head" onClick={() => setOpen(!isOpen)} aria-expanded={isOpen}>
        <Icon name={isOpen ? "down" : "right"} size={14} className="caret" /> <span>{title}</span>
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

export function day(iso?: string | Date | null) {
  if (!iso) return "";
  const d = iso instanceof Date ? iso : new Date(iso);
  return isNaN(d.getTime()) ? String(iso) : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** "3 h ago", "in 12 days": coarse, for card footers. */
export function relTime(iso?: string | Date | null) {
  if (!iso) return "";
  const d = iso instanceof Date ? iso : new Date(iso);
  if (isNaN(d.getTime())) return "";
  const s = (d.getTime() - Date.now()) / 1000;
  const a = Math.abs(s);
  const [n, u] =
    a < 60 ? [0, "now"] : a < 3600 ? [Math.round(a / 60), "min"] : a < 86400 ? [Math.round(a / 3600), "h"] : [Math.round(a / 86400), "day"];
  if (u === "now") return "just now";
  const unit = u === "day" && n !== 1 ? "days" : u;
  return s < 0 ? `${n} ${unit} ago` : `in ${n} ${unit}`;
}

/** "…9c31a0": the tail of a run id, which is what people tell apart. */
export const shortId = (id: string) => `…${id.slice(-6)}`;

export function ErrorNote({ error }: { error: string | null }) {
  return error ? (
    <p className="error" role="alert">
      {error}
    </p>
  ) : null;
}

export function Loading() {
  return <p className="muted pad">Loading…</p>;
}

/** Back link and breadcrumb bar at the top of a full-width page (ask, run). */
export function TopBar({ children, back = "/", backLabel = "Board" }: { children?: ReactNode; back?: string; backLabel?: string }) {
  return (
    <header className="topbar">
      <Link className="backlink" to={back}>
        <Icon name="back" /> {backLabel}
      </Link>
      {children}
    </header>
  );
}
