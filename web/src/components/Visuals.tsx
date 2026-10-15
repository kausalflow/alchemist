// What people see of the agent's work as it happens: figures as they are made, and a preview of each query result.

import { fileUrl, type QueryPreview, type Step } from "../api";
import { day } from "./bits";

/** Figures a step made, as thumbnails that open full size. */
export function StepFigures({ runId, figures }: { runId: string; figures: string[] }) {
  return (
    <div className="figs">
      {figures.map((f) => (
        <a key={f} href={fileUrl(runId, f)} target="_blank" rel="noreferrer" className="fig" title="Open full size">
          <img src={fileUrl(runId, f)} alt={`Figure ${f.replace(/^figures\//, "")}`} loading="lazy" />
        </a>
      ))}
    </div>
  );
}

/** A small line of a numeric column in row order; gaps (empty buckets) break the line. */
export function Sparkline({ values, width = 120, height = 28 }: { values: (number | null)[]; width?: number; height?: number }) {
  const nums = values.filter((v): v is number => v !== null);
  if (nums.length < 2) return <svg width={width} height={height} aria-hidden="true" />;
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const x = (i: number) => (values.length === 1 ? 0 : (i / (values.length - 1)) * (width - 2) + 1);
  const y = (v: number) => (hi === lo ? height / 2 : height - 2 - ((v - lo) / (hi - lo)) * (height - 4));
  const runs: string[] = [];
  let cur: string[] = [];
  values.forEach((v, i) => {
    if (v === null) {
      if (cur.length) runs.push(cur.join(" "));
      cur = [];
    } else cur.push(`${x(i).toFixed(1)},${y(v).toFixed(1)}`);
  });
  if (cur.length) runs.push(cur.join(" "));
  return (
    <svg className="spark" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      {runs.map((pts, i) =>
        pts.includes(" ") ? (
          <polyline key={i} points={pts} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" />
        ) : (
          <circle key={i} cx={pts.split(",")[0]} cy={pts.split(",")[1]} r={1.5} fill="currentColor" />
        ),
      )}
    </svg>
  );
}

const fmt = (v: unknown) =>
  typeof v === "number" ? (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3)) : String(v ?? "");

/** A query result's shape: rows, time range, and a sparkline with range and gaps for each numeric column. */
export function QueryPreviewView({ p }: { p: QueryPreview }) {
  const stats = p.stats ?? {};
  const times = Object.entries(stats).filter(([, s]) => typeof s.min === "string");
  const sparks = Object.entries(p.spark ?? {});
  return (
    <div className="qprev">
      <div className="small muted">
        {p.rows?.toLocaleString()} rows · {Object.keys(p.columns ?? {}).length} columns
        {times.map(([c, s]) => (
          <span key={c}>
            {" "}
            · <span className="mono">{c}</span> {day(s.min as string)} to {day(s.max as string)}
          </span>
        ))}
      </div>
      {sparks.length > 0 && (
        <div className="qprev-grid">
          {sparks.map(([c, vals]) => {
            const s = stats[c] ?? {};
            const nulls = (s.nulls as number) ?? 0;
            return (
              <div key={c} className="qprev-col">
                <span className="mono tiny">{c}</span>
                <span className="accent-text">
                  <Sparkline values={vals} />
                </span>
                <span className="tiny faint">
                  {fmt(s.min)} to {fmt(s.max)}
                  {nulls > 0 && <span className="warn-text"> · {nulls.toLocaleString()} missing</span>}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Every figure made so far, newest last, each linked to the step that made it. */
export function FigureStrip({ runId, steps }: { runId: string; steps: Step[] }) {
  let n = 0;
  const figs: { step: number; path: string; purpose: string }[] = [];
  for (const s of steps) {
    if (s.type === "tool") n += 1;
    for (const f of s.meta?.figures ?? [])
      figs.push({
        step: n,
        path: f,
        purpose: (s.input?.purpose as string) ?? "",
      });
  }
  if (!figs.length) return null;
  return (
    <section aria-labelledby="figstrip" className="panel">
      <div className="panel-head">
        <h2 id="figstrip" className="h-panel">
          Figures so far
        </h2>
        <span className="muted small">
          {figs.length} {figs.length === 1 ? "figure" : "figures"}, newest last. Click one to see the step that made it.
        </span>
      </div>
      <div className="figstrip">
        {figs.map((f) => (
          <a key={f.path} href={`#step-${f.step}`} className="figstrip-item">
            <img src={fileUrl(runId, f.path)} alt={f.purpose || f.path} loading="lazy" />
            <span className="tiny muted">
              <span className="mono">#{f.step}</span> {f.purpose}
            </span>
          </a>
        ))}
      </div>
    </section>
  );
}
