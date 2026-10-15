import { NavLink, Route, Routes } from "react-router-dom";
import { SessionProvider, useSession } from "./session";
import AskPage from "./pages/Ask";
import HistoryPage from "./pages/History";
import RunPage from "./pages/Run";
import CatalogPage from "./pages/Catalog";
import DatasetPage from "./pages/Dataset";
import CatalogAdminPage from "./pages/CatalogAdmin";
import AboutPage from "./pages/About";
import SignIn from "./pages/SignIn";

export default function App() {
  return (
    <SessionProvider>
      <Shell />
    </SessionProvider>
  );
}

function Shell() {
  const { me, meta, loading, signedOut, signOut } = useSession();
  if (loading && !me) return <div className="center muted">Loading…</div>;
  if (signedOut || !me) return <SignIn />;
  return (
    <div className="app">
      <header className="topbar">
        <NavLink to="/" className="brand">
          Alchemist
        </NavLink>
        <nav>
          <NavLink to="/" end>
            Ask
          </NavLink>
          <NavLink to="/runs">Runs</NavLink>
          <NavLink to="/catalog">Catalog</NavLink>
          {me.catalog_admin && <NavLink to="/catalog-admin">Catalog admin</NavLink>}
          <NavLink to="/about">About</NavLink>
        </nav>
        <div className="who">
          <span title={`Plants: ${me.readable_plants.join(", ")} · Workspaces: ${me.workspaces.join(", ")}`}>
            {me.name}
            <span className="muted"> · {me.global || me.admin ? "all plants" : me.readable_plants.join(", ")}</span>
          </span>
          {meta?.auth === "dev" && (
            <button className="link" onClick={signOut}>
              switch user
            </button>
          )}
        </div>
      </header>
      {meta?.models.scripted && (
        <div className="banner">
          Demo mode: runs play the offline script for the demo question instead of calling a model.
        </div>
      )}
      <main>
        <Routes>
          <Route path="/" element={<AskPage />} />
          <Route path="/runs" element={<HistoryPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
          <Route path="/catalog" element={<CatalogPage />} />
          <Route path="/catalog/:datasetId" element={<DatasetPage />} />
          <Route path="/catalog-admin" element={<CatalogAdminPage />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="*" element={<p>Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}
