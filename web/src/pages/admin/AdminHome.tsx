import { Link } from "react-router-dom";
import { Loading } from "../../components/bits";
import { needsAttention, plantTitle, useAdmin } from "./AdminLayout";

/** /admin: every plant catalog at a glance. */
export default function AdminHome() {
  const { plants } = useAdmin();
  return (
    <div className="page">
      <header className="page-head">
        <div className="grow">
          <div className="crumb">Admin</div>
          <h1>Plant catalogs</h1>
        </div>
      </header>
      <div className="section stack">
        <p className="muted measure">
          Each plant has its own catalog: data sources, documents, reference facts and lines. Questions scoped to a plant search its catalog
          and the shared one.
        </p>
        {!plants && <Loading />}
        <div className="grid">
          {plants?.map((p) => (
            <Link key={p.id} to={`/admin/plants/${encodeURIComponent(p.id)}`} className="tile">
              <div className="spread">
                <span className="strong-ish">{plantTitle(p)}</span>
                <span className={`pill ${needsAttention(p) ? "warn" : "ok"}`}>{needsAttention(p) ? "Needs attention" : "In order"}</span>
              </div>
              {p.description && <p className="small muted">{p.description}</p>}
              <div className="small faint mono">
                {p.datasets} data · {p.documents} docs · {p.facts} facts · {p.lines} lines
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
