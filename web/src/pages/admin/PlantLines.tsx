import { useState } from "react";
import { api, type Line } from "../../api";
import { ErrorNote } from "../../components/bits";
import Icon from "../../components/icons";
import { usePlant } from "./PlantLayout";

/** Lines and equipment: the plant's lines, which documents and questions refer to by id. */
export default function PlantLines() {
  const { detail, reload } = usePlant();
  const [lines, setLines] = useState<Line[]>(() => detail.lines.map((l) => ({ ...l })));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const edited = JSON.stringify(lines) !== JSON.stringify(detail.lines);
  const set = (i: number, k: keyof Line, v: string) => setLines(lines.map((l, j) => (j === i ? { ...l, [k]: v } : l)));

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const saved = await api.put<Line[]>(`/admin/plants/${encodeURIComponent(detail.plant.id)}/lines`, { lines });
      setLines(saved.map((l) => ({ ...l })));
      setMsg(`Saved ${saved.length} ${saved.length === 1 ? "line" : "lines"}.`);
      reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="stack measure-wide">
      <p className="muted small">
        Questions are scoped to a line by its id (for example <span className="mono">L2</span>), and documents say which lines they apply to.
      </p>
      <div className="table-wrap">
        <table className="table edit-table">
          <thead>
            <tr>
              <th scope="col">Id</th>
              <th scope="col">Name</th>
              <th scope="col">Description</th>
              <th scope="col">
                <span className="sr-only">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {lines.length === 0 && (
              <tr>
                <td colSpan={4} className="muted">
                  No lines yet.
                </td>
              </tr>
            )}
            {lines.map((l, i) => (
              <tr key={i}>
                <td>
                  <input className="mono" aria-label={`Line ${i + 1} id`} value={l.id} onChange={(e) => set(i, "id", e.target.value)} required />
                </td>
                <td>
                  <input aria-label={`Line ${i + 1} name`} value={l.name} onChange={(e) => set(i, "name", e.target.value)} />
                </td>
                <td>
                  <input aria-label={`Line ${i + 1} description`} value={l.description} onChange={(e) => set(i, "description", e.target.value)} />
                </td>
                <td>
                  <button
                    type="button"
                    className="icon-btn danger"
                    aria-label={`Remove line ${l.id || i + 1}`}
                    onClick={() => setLines(lines.filter((_, j) => j !== i))}
                  >
                    <Icon name="trash" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ErrorNote error={error} />
      {msg && (
        <p className="ok-text small" role="status">
          {msg}
        </p>
      )}
      <div className="row-center">
        <button type="button" className="ghost" onClick={() => setLines([...lines, { id: "", name: "", description: "" }])}>
          <Icon name="plus" size={14} /> Add a line
        </button>
        <button className="primary" disabled={busy || !edited}>
          {busy ? "Saving…" : "Save lines"}
        </button>
        {edited && (
          <button type="button" className="ghost" onClick={() => setLines(detail.lines.map((l) => ({ ...l })))}>
            Undo changes
          </button>
        )}
      </div>
    </form>
  );
}
