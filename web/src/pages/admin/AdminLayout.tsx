import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate, useOutletContext } from "react-router-dom";
import { api, type PlantSummary } from "../../api";
import { useApi, useSession } from "../../session";
import { DemoBanner } from "../../components/Shell";
import { ErrorNote } from "../../components/bits";
import Icon from "../../components/icons";

export interface AdminCtx {
  plants: PlantSummary[] | null;
  reloadPlants: () => void;
}

export const useAdmin = () => useOutletContext<AdminCtx>();

export const plantTitle = (p: Pick<PlantSummary, "id" | "name">) =>
  p.id === "global" ? "All plants (shared)" : p.name && p.name !== p.id ? `${p.id} · ${p.name}` : p.id;
export const needsAttention = (p: PlantSummary) => p.datasets_waiting + p.datasets_unusable + p.documents_overdue > 0;

/** The admin area: its own rail (plant catalogs, organisation) and the page beside it. Admins only. */
export default function AdminLayout() {
  const { me, meta } = useSession();
  const allowed = me!.catalog_admin || me!.admin;
  const { data: plants, error, reload } = useApi<PlantSummary[]>(allowed ? "/admin/plants" : null);
  const [menu, setMenu] = useState(false);

  if (!allowed)
    return (
      <div className="fullpage">
        <header className="topbar">
          <Link className="backlink" to="/">
            <Icon name="back" /> Back to the board
          </Link>
        </header>
        <div className="pad-page measure stack">
          <h1 className="title">Admins only</h1>
          <p className="muted">
            The admin area manages plant catalogs, documents and lines. You need the catalog admin or admin role to open it; ask an admin
            if you think you should have it.
          </p>
        </div>
      </div>
    );

  return (
    <div className="app">
      <nav className={`rail admin-rail ${menu ? "open" : ""}`} aria-label="Admin">
        <div className="rail-top">
          <Link className="backlink" to="/">
            <Icon name="back" /> Back to the board
          </Link>
          <button
            type="button"
            className="icon-btn rail-toggle"
            aria-expanded={menu}
            aria-controls="admin-more"
            aria-label={menu ? "Hide menu" : "Show menu"}
            onClick={() => setMenu(!menu)}
          >
            <Icon name={menu ? "up" : "down"} />
          </button>
        </div>
        <div className="admin-title">
          <span className="strong">Admin</span>
          <span className="pill plan">
            <Icon name="shield" size={12} /> Only admins see this area
          </span>
        </div>
        <div id="admin-more" className="rail-more">
          <div className="rail-section">
            <div className="eyebrow">Plant catalogs</div>
            <ErrorNote error={error} />
            {plants?.map((p) => (
              <NavLink key={p.id} to={`/admin/plants/${encodeURIComponent(p.id)}`} className="proj">
                <span
                  className={`pdot ${needsAttention(p) ? "warn" : "ok"}`}
                  role="img"
                  aria-label={needsAttention(p) ? "needs attention" : "all in order"}
                />
                <span className={p.id === "global" ? "" : "mono-id"}>{plantTitle(p)}</span>
              </NavLink>
            ))}
            <AddPlant onDone={reload} />
          </div>
          <div className="rail-section">
            <div className="eyebrow">Organisation</div>
            <NavLink to="/admin/workspaces" className="proj">
              Workspaces
            </NavLink>
            <NavLink to="/admin/access" className="proj">
              Access groups
            </NavLink>
            <NavLink to="/admin/models" className="proj">
              Models and limits
            </NavLink>
          </div>
        </div>
      </nav>
      <div className="main-col">
        <DemoBanner scripted={!!meta?.models.scripted} />
        <main id="main" className="content">
          <Outlet context={{ plants, reloadPlants: reload } satisfies AdminCtx} />
        </main>
      </div>
    </div>
  );
}

function AddPlant({ onDone }: { onDone: () => void }) {
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ id: "", name: "", description: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const p = await api.post<PlantSummary>("/admin/plants", f);
      setF({ id: "", name: "", description: "" });
      setOpen(false);
      onDone();
      nav(`/admin/plants/${encodeURIComponent(p.id)}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!open)
    return (
      <button type="button" className="ghost block add-plant" onClick={() => setOpen(true)}>
        <Icon name="plus" size={14} /> Add a plant
      </button>
    );
  return (
    <form className="mini-form stack-sm" onSubmit={submit} aria-label="Add a plant">
      <label className="field">
        <span className="field-label">Plant id</span>
        <input
          className="mono"
          value={f.id}
          onChange={(e) => setF({ ...f, id: e.target.value })}
          pattern="[A-Za-z0-9][A-Za-z0-9_\-]{0,31}"
          title="letters, digits, dash and underscore; up to 32 characters"
          required
          placeholder="P04"
        />
      </label>
      <label className="field">
        <span className="field-label">Name</span>
        <input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required placeholder="Coating plant" />
      </label>
      <label className="field">
        <span className="field-label">Description</span>
        <textarea rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </label>
      <ErrorNote error={error} />
      <div className="row-center">
        <button className="primary small" disabled={busy}>
          Add plant
        </button>
        <button type="button" className="ghost small" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
