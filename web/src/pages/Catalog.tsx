import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { DatasetSummary } from "../api";
import { useApi, useSession } from "../session";
import { Chip, ErrorNote } from "../components/bits";

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
    <div>
      <h1>Data catalog</h1>
      <p className="muted">
        Approved datasets for the plants you may read ({me!.readable_plants.join(", ")}). These cards are what the agent sees: units,
        grain, time zone, join keys and known quirks.
      </p>
      <input className="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search: resin melt flow, barrel temperature…" />
      <ErrorNote error={error} />
      <div className="grid">
        {data?.map((d) => (
          <Link key={d.dataset} to={`/catalog/${d.dataset}`} className="card dscard">
            <div className="spread">
              <strong>{d.dataset}</strong>
              <span className="small muted">
                {d.plant} · card v{d.card_version}
              </span>
            </div>
            <p className="small">{d.description || <span className="muted">No description.</span>}</p>
            <div className="small muted">
              {d.grain && <>grain {d.grain} · </>}
              {d.columns.length} columns
            </div>
            {!!d.missing_facts?.length && <Chip tone="warn">{d.missing_facts.length} missing facts</Chip>}
          </Link>
        ))}
        {data && data.length === 0 && <p className="muted">No approved dataset matches.</p>}
      </div>
    </div>
  );
}
