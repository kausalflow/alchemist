import { Navigate, Route, Routes } from "react-router-dom";
import { SessionProvider, useSession } from "./session";
import { WorkspaceProvider } from "./workspace";
import Shell, { FocusLayout } from "./components/Shell";
import BoardPage from "./pages/Board";
import WaitingPage from "./pages/Waiting";
import AskPage from "./pages/Ask";
import ReportsPage from "./pages/Reports";
import RunPage from "./pages/Run";
import CatalogPage from "./pages/Catalog";
import DatasetPage from "./pages/Dataset";
import AboutPage from "./pages/About";
import SignIn from "./pages/SignIn";
import AdminLayout from "./pages/admin/AdminLayout";
import AdminHome from "./pages/admin/AdminHome";
import PlantLayout from "./pages/admin/PlantLayout";
import PlantOverview from "./pages/admin/PlantOverview";
import PlantData from "./pages/admin/PlantData";
import PlantKnowledge from "./pages/admin/PlantKnowledge";
import PlantLines from "./pages/admin/PlantLines";
import { AccessPage, ModelsPage, WorkspacesPage } from "./pages/admin/Org";

export default function App() {
  return (
    <SessionProvider>
      <Gate />
    </SessionProvider>
  );
}

function Gate() {
  const { me, loading, signedOut } = useSession();
  if (loading && !me) return <div className="center muted">Loading…</div>;
  if (signedOut || !me) return <SignIn />;
  return (
    <WorkspaceProvider key={me.id}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/" element={<BoardPage />} />
          <Route path="/waiting" element={<WaitingPage />} />
          <Route path="/runs" element={<ReportsPage />} />
          <Route path="/catalog" element={<CatalogPage />} />
          <Route path="/catalog/:datasetId" element={<DatasetPage />} />
          <Route path="/catalog-admin" element={<Navigate to="/admin" replace />} />
          <Route path="/about" element={<AboutPage />} />
          <Route path="*" element={<p className="pad-page">Page not found.</p>} />
        </Route>
        <Route element={<FocusLayout />}>
          <Route path="/ask" element={<AskPage />} />
          <Route path="/runs/:runId" element={<RunPage />} />
        </Route>
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<AdminHome />} />
          <Route path="workspaces" element={<WorkspacesPage />} />
          <Route path="access" element={<AccessPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="plants/:plantId" element={<PlantLayout />}>
            <Route index element={<PlantOverview />} />
            <Route path="data" element={<PlantData />} />
            <Route path="knowledge" element={<PlantKnowledge />} />
            <Route path="lines" element={<PlantLines />} />
          </Route>
        </Route>
      </Routes>
    </WorkspaceProvider>
  );
}
