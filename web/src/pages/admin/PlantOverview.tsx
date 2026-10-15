import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type PlantPreview } from "../../api";
import { ErrorNote, day } from "../../components/bits";
import { usePlant } from "./PlantLayout";

interface Todo {
  key: string;
  tag: string;
  tone: string;
  what: string;
  why: string;
  action: string;
  to: string;
}

/** Plant overview: what is in the catalog, what needs an admin, and a test of what an agent would find. */
export default function PlantOverview() {
  const { detail } = usePlant();
  const p = detail.plant;
  const base = `/admin/plants/${encodeURIComponent(p.id)}`;
  const plantName = p.id === "global" ? "any plant" : p.id;

  const stats = [
    {
      label: "Data sources",
      n: p.datasets,
      note: p.datasets_waiting || p.datasets_unusable ? [p.datasets_waiting && `${p.datasets_waiting} waiting for review`, p.datasets_unusable && `${p.datasets_unusable} not usable yet`].filter(Boolean).join(", ") : "all reviewed",
      warn: p.datasets_waiting + p.datasets_unusable > 0,
      to: `${base}/data`,
    },
    {
      label: "Documents",
      n: p.documents,
      note: p.documents_overdue ? `${p.documents_overdue} past review date` : "all current",
      warn: p.documents_overdue > 0,
      to: `${base}/knowledge`,
    },
    { label: "Reference facts", n: p.facts, note: "used directly by analysis code", warn: false, to: `${base}/knowledge` },
    { label: "Lines", n: p.lines, note: detail.lines.length ? detail.lines.map((l) => l.id).slice(0, 6).join(", ") : "none defined", warn: false, to: `${base}/lines` },
  ];

  const todos: Todo[] = [];
  for (const d of detail.datasets) {
    if (d.waiting_for_review)
      todos.push({
        key: `w-${d.dataset}`,
        tag: "Data source",
        tone: "data",
        what: `${d.dataset}: v${d.latest_version} waiting for review`,
        why: d.approved_version ? "A new description was drafted. Check it before agents use it." : "Agents cannot use it until its first description is approved.",
        action: "Review",
        to: `${base}/data?ds=${encodeURIComponent(d.dataset)}`,
      });
    else if (d.no_metadata_file)
      todos.push({
        key: `m-${d.dataset}`,
        tag: "Data source",
        tone: "data",
        what: `${d.dataset} has no metadata file`,
        why: "Agents can see it exists but not what its columns mean.",
        action: "Describe it",
        to: `${base}/data?ds=${encodeURIComponent(d.dataset)}`,
      });
  }
  for (const d of detail.documents.filter((x) => x.overdue))
    todos.push({
      key: `d-${d.id}`,
      tag: "Document",
      tone: "doc",
      what: `${d.title} is past its review date`,
      why: `Review was due ${day(d.review_date)}. Agents still use it but flag it as possibly out of date.`,
      action: d.read_only ? "Open" : "Update",
      to: `${base}/knowledge?doc=${encodeURIComponent(d.id)}`,
    });
  for (const s of detail.suggestions.filter((x) => (x.status ?? "open") === "open"))
    todos.push({
      key: `s-${s.id}`,
      tag: "Suggestion",
      tone: "fact",
      what: `${s.raised_by}: ${s.text}`,
      why: `About ${s.dataset_id} (${s.kind}), ${day(s.created_at)}.`,
      action: "Open",
      to: `${base}/data?ds=${encodeURIComponent(s.dataset_id)}`,
    });

  return (
    <div className="split">
      <div className="split-main stack">
        <div className="stats">
          {stats.map((s) => (
            <Link key={s.label} to={s.to} className="stat">
              <span className="small muted">{s.label}</span>
              <span className="stat-n mono">{s.n}</span>
              <span className={`small ${s.warn ? "warn-text" : "faint"}`}>{s.note}</span>
            </Link>
          ))}
        </div>
        <section aria-labelledby="todo" className="panel">
          <h2 id="todo" className="h-panel panel-head">
            Needs your attention
          </h2>
          {todos.length === 0 && <p className="muted small pad-row">Nothing needs you right now.</p>}
          {todos.map((t) => (
            <div key={t.key} className="todo">
              <span className={`tag ${t.tone}`}>{t.tag}</span>
              <div className="grow">
                <div className="strong-ish">{t.what}</div>
                <div className="small muted">{t.why}</div>
              </div>
              <Link className="button small" to={t.to}>
                {t.action}
              </Link>
            </div>
          ))}
        </section>
        <section className="panel pad">
          <h2 className="h-panel">How agents use this catalog</h2>
          <p className="muted small">
            {p.id === "global"
              ? "This shared catalog is searched for every question, whichever plant it is scoped to. "
              : `When someone asks a question scoped to ${p.id}, the agent searches only ${p.id} and the shared catalog. `}
            It sees approved data sources, current documents and reference facts. Each run records the exact version of everything it used.
          </p>
        </section>
      </div>
      <aside className="split-side">
        <Preview plantId={p.id} plantName={plantName} />
      </aside>
    </div>
  );
}

function Preview({ plantId, plantName }: { plantId: string; plantName: string }) {
  const [q, setQ] = useState("");
  const [res, setRes] = useState<PlantPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const test = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setRes(await api.post<PlantPreview>(`/admin/plants/${encodeURIComponent(plantId)}/preview`, { question: q }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-labelledby="pv" className="panel pad stack-sm preview">
      <h2 id="pv" className="h-panel">
        Test what an agent would find
      </h2>
      <form onSubmit={test} className="stack-sm">
        <label className="field">
          <span className="field-label">A question someone at {plantName} might ask</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} required minLength={3} placeholder="Why did first-pass yield drop on Line 2?" />
        </label>
        <button className="primary" disabled={busy}>
          {busy ? "Searching…" : "Test"}
        </button>
      </form>
      <ErrorNote error={error} />
      {res && (
        <div className="stack-sm" aria-live="polite">
          <Group title="Data sources" empty="No approved data source matches.">
            {res.datasets.map((d) => (
              <Hit key={d.dataset} name={d.dataset} mono note={`${d.description || "no description"} · ${d.plant} · card v${d.version}`} />
            ))}
          </Group>
          <Group title="Documents" empty="No document section matches.">
            {res.documents.map((d, i) => (
              <Hit
                key={`${d.doc}-${i}`}
                name={d.title}
                note={`${d.section} · ${d.plant} · v${d.version}${d.stale ? " · past review date" : ""}`}
                warn={d.stale}
                detail={d.text}
              />
            ))}
          </Group>
          <Group title="Reference facts" empty="No reference facts for this plant.">
            {res.facts.map((f) => (
              <Hit key={f.name} name={f.name} mono note={`${f.plant} · v${f.version}`} />
            ))}
          </Group>
          <Group title="Lines" empty="No lines defined.">
            {res.lines.map((l) => (
              <Hit key={l.id} name={l.id} mono note={l.name} />
            ))}
          </Group>
          <p className="faint small">Missing something? Add the document or data source, or fix its description, then test again.</p>
        </div>
      )}
    </section>
  );
}

function Group({ title, empty, children }: { title: string; empty: string; children: React.ReactNode[] }) {
  return (
    <div>
      <div className="eyebrow">{title}</div>
      {children.length ? children : <p className="faint small">{empty}</p>}
    </div>
  );
}

function Hit({ name, note, mono, warn, detail }: { name: string; note: string; mono?: boolean; warn?: boolean; detail?: string }) {
  return (
    <div className="hit">
      <span className={mono ? "mono accent-text" : "strong-ish"}>{name}</span>
      <span className={`small ${warn ? "warn-text" : "muted"}`}>{note}</span>
      {detail && <span className="small faint clamp">{detail}</span>}
    </div>
  );
}
