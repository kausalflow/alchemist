import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { DatasetSummary } from "../api";
import { useApi, useSession } from "../session";
import { ErrorNote, Loading } from "../components/bits";
import Icon from "../components/icons";

export default function CatalogPage() {
  const { me } = useSession();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 300);
    return () => clearTimeout(t);
  }, [q]);
  const { data, error } = useApi<DatasetSummary[]>(query ? `/datasets?q=${encodeURIComponent(query)}` : "/datasets");

  return (
    <div className="page">
      <header className="page-head">
        <div className="grow">
          <div className="crumb">Plants you may read: {me!.readable_plants.join(", ") || "none"}</div>
          <h1>Data catalog</h1>
        </div>
        <label className="searchbox wide">
          <Icon name="search" />
          <span className="sr-only">Search the catalog</span>
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Resin melt flow, barrel temperature…" />
        </label>
      </header>
      <div className="section stack">
        <p className="muted measure">
          Approved data sources. These cards are what the agent sees: units, grain, time zone, join keys and known quirks.
        </p>
        <ErrorNote error={error} />
        {!data && !error && <Loading />}
        <div className="grid">
          {data?.map((d) => (
            <Link key={d.dataset} to={`/catalog/${d.dataset}`} className="tile">
              <div className="spread">
                <span className="mono accent-text strong-ish">{d.dataset}</span>
                <span className="small faint mono">
                  {d.plant} · v{d.card_version}
                </span>
              </div>
              <p className="small">{d.description || <span className="faint">No description.</span>}</p>
              <div className="small faint">
                {d.grain && <>{d.grain} · </>}
                {d.columns.length} columns
              </div>
              {!!d.missing_facts?.length && <span className="pill warn">{d.missing_facts.length} missing facts</span>}
            </Link>
          ))}
        </div>
        {data && data.length === 0 && <p className="muted">No approved data source matches.</p>}
      </div>
    </div>
  );
}
