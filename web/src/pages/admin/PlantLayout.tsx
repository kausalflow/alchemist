import { NavLink, Outlet, useOutletContext, useParams } from "react-router-dom";
import type { PlantDetail } from "../../api";
import { useApi } from "../../session";
import { ErrorNote, Loading } from "../../components/bits";
import { plantTitle, useAdmin } from "./AdminLayout";

export interface PlantCtx {
  detail: PlantDetail;
  reload: () => void;
}

export const usePlant = () => useOutletContext<PlantCtx>();

/** A plant catalog: header, section tabs, and the section below. */
export default function PlantLayout() {
  const { plantId } = useParams();
  const { reloadPlants } = useAdmin();
  const { data, error, reload } = useApi<PlantDetail>(`/admin/plants/${encodeURIComponent(plantId!)}`);
  const base = `/admin/plants/${encodeURIComponent(plantId!)}`;
  const fresh = data && data.plant.id === plantId ? data : null; // the previous plant's data while switching
  const p = fresh?.plant;
  const reloadBoth = () => {
    reload();
    reloadPlants();
  };

  return (
    <div className="page">
      <header className="page-head admin-head">
        <div className="grow">
          <div className="crumb">Plant catalog</div>
          <h1>{p ? plantTitle(p) : plantId}</h1>
          {p && (
            <div className="small muted">
              {p.id === "global" ? (
                "Every plant's questions also search this shared catalog."
              ) : (
                <>
                  Readable by group <span className="mono">{p.readers_group || "none"}</span>
                  {!p.in_access_model && <span className="warn-text"> · not in the access model yet, so nobody can read its data</span>}
                </>
              )}
              {p.description && ` · ${p.description}`}
            </div>
          )}
        </div>
        <nav className="subtabs" aria-label="Plant catalog sections">
          <NavLink to={base} end>
            Overview
          </NavLink>
          <NavLink to={`${base}/data`}>Data sources</NavLink>
          <NavLink to={`${base}/knowledge`}>Documents and facts</NavLink>
          <NavLink to={`${base}/lines`}>Lines and equipment</NavLink>
        </nav>
      </header>
      <div className="section">
        <ErrorNote error={error} />
        {!fresh && !error && <Loading />}
        {fresh && <Outlet context={{ detail: fresh, reload: reloadBoth } satisfies PlantCtx} />}
      </div>
    </div>
  );
}
