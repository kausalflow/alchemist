import { useState } from "react";
import { Link, NavLink, Outlet, useLocation, useSearchParams } from "react-router-dom";
import type { RunSummary } from "../api";
import { useApi, useSession } from "../session";
import { useWorkspace } from "../workspace";
import Icon from "./icons";
import { waitingOnMe } from "./Waiting";

/** The app frame: left rail (brand, workspace, ask, nav, projects, who you are) and the page beside it. */
export default function Shell() {
  const { meta } = useSession();
  return (
    <div className="app">
      <Rail />
      <div className="main-col">
        <DemoBanner scripted={!!meta?.models.scripted} />
        <main id="main" className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/** Full-width pages with their own back bar (ask, run): no rail, so the work gets the room. */
export function FocusLayout() {
  const { meta } = useSession();
  return (
    <div className="focus">
      <DemoBanner scripted={!!meta?.models.scripted} />
      <main id="main">
        <Outlet />
      </main>
    </div>
  );
}

export function DemoBanner({ scripted }: { scripted: boolean }) {
  if (!scripted) return null;
  return <div className="banner">Demo mode: runs play the offline script for the demo question instead of calling a model.</div>;
}

export function Brand() {
  return (
    <Link to="/" className="brand">
      <Icon name="brand" size={26} />
      <span>Alchemist</span>
    </Link>
  );
}

function Rail() {
  const { me, meta, signOut } = useSession();
  const { workspaces, current, ws, setCurrent } = useWorkspace();
  const loc = useLocation();
  const [params] = useSearchParams();
  const [open, setOpen] = useState(false);
  const { data: runs } = useApi<RunSummary[]>("/runs", [loc.pathname]);
  const waiting = runs && me ? waitingOnMe(runs, me).length : 0;
  const project = loc.pathname === "/" ? params.get("project") ?? "" : null;
  const isAdmin = me!.catalog_admin || me!.admin;

  return (
    <nav className={`rail ${open ? "open" : ""}`} aria-label="Main">
      <div className="rail-top">
        <Brand />
        <button
          type="button"
          className="icon-btn rail-toggle"
          aria-expanded={open}
          aria-controls="rail-more"
          aria-label={open ? "Hide menu" : "Show menu"}
          onClick={() => setOpen(!open)}
        >
          <Icon name={open ? "up" : "down"} />
        </button>
      </div>

      <label className="ws-switch">
        <span className="ws-label">Workspace</span>
        <select value={current} onChange={(e) => setCurrent(e.target.value)} aria-label="Switch workspace">
          {!workspaces?.length && <option value="">No workspace</option>}
          {workspaces?.map((w) => (
            <option key={w.name} value={w.name}>
              {w.name}
            </option>
          ))}
        </select>
        <Icon name="down" size={14} className="ws-caret" />
      </label>

      <Link to="/ask" className="button primary block">
        <Icon name="plus" /> Ask a question
      </Link>

      <div id="rail-more" className="rail-more">
        <div className="rail-nav">
          <NavLink to="/" end className={() => (loc.pathname === "/" ? "active" : "")}>
            Board
          </NavLink>
          <NavLink to="/waiting">
            Waiting on you {waiting > 0 && <span className="count warn">{waiting}</span>}
          </NavLink>
          <NavLink to="/runs" end>
            Reports
          </NavLink>
          <NavLink to="/catalog">Data catalog</NavLink>
          {isAdmin && (
            <NavLink to="/admin" className={({ isActive }) => `sep ${isActive ? "active" : ""}`}>
              Admin <span className="count plan">admins only</span>
            </NavLink>
          )}
        </div>

        <div className="rail-section">
          <div className="eyebrow">Projects{ws ? ` in ${ws.name}` : ""}</div>
          <Link to="/" className={`proj ${project === "" ? "active" : ""}`}>
            <span className="pdot" aria-hidden="true" />
            All projects
          </Link>
          {ws?.projects.map((p) => (
            <Link key={p.name} to={`/?project=${encodeURIComponent(p.name)}`} className={`proj mono ${project === p.name ? "active" : ""}`}>
              <span className="pdot" aria-hidden="true" />
              {p.name}
            </Link>
          ))}
          {ws && ws.projects.length === 0 && <p className="faint small pad-x">No projects yet. Asking a question creates one.</p>}
        </div>

        <div className="rail-foot">
          <div className="who-name">{me!.name}</div>
          <div className="faint small">
            Can read data from {me!.global || me!.admin ? "all plants" : me!.readable_plants.join(", ") || "no plant"}
          </div>
          <div className="faint small">Workspaces: {me!.workspaces.join(", ") || "none"}</div>
          {(me!.admin || me!.catalog_admin) && <div className="faint small">{me!.admin ? "Admin" : "Catalog admin"}</div>}
          <div className="foot-links small">
            <Link to="/about">About this deployment</Link>
            {meta?.auth === "dev" && (
              <button type="button" className="link" onClick={signOut}>
                Switch user
              </button>
            )}
          </div>
        </div>
      </div>
    </nav>
  );
}
