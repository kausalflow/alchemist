import { useState } from "react";
import { api, type DevUser, type ModelRole, type ModelSettings, type RunLimit, type Workspace } from "../../api";
import { useApi, useSession } from "../../session";
import { day, ErrorNote, Loading } from "../../components/bits";
import { plantTitle, useAdmin } from "./AdminLayout";

function Head({ title, crumb = "Organisation" }: { title: string; crumb?: string }) {
  return (
    <header className="page-head">
      <div className="grow">
        <div className="crumb">{crumb}</div>
        <h1>{title}</h1>
      </div>
    </header>
  );
}

/** Workspaces and their plants and projects (read-only: defined in access.yaml or by identity-provider groups). */
export function WorkspacesPage() {
  const { data, error } = useApi<Workspace[]>("/workspaces");
  return (
    <div className="page">
      <Head title="Workspaces" />
      <div className="section stack">
        <p className="muted measure">
          Workspaces group people and the plants their questions may use. They are defined in the access model (access.yaml, or groups in the
          identity provider), not here. This list shows the workspaces you can see.
        </p>
        <ErrorNote error={error} />
        {!data && !error && <Loading />}
        {data && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Workspace</th>
                  <th scope="col">Plants</th>
                  <th scope="col">Projects</th>
                </tr>
              </thead>
              <tbody>
                {data.map((w) => (
                  <tr key={w.name}>
                    <td className="strong-ish">{w.name}</td>
                    <td className="mono">{w.plants.join(", ")}</td>
                    <td className="mono small">{w.projects.map((p) => p.name).join(", ") || <span className="faint sans">none yet</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

/** Who may read which plant: reader groups per plant and, in local development, the users in access.yaml. */
export function AccessPage() {
  const { meta } = useSession();
  const { plants } = useAdmin();
  const { data: users, error } = useApi<DevUser[]>(meta?.auth === "dev" ? "/dev/users" : null);
  return (
    <div className="page">
      <Head title="Access groups" />
      <div className="section stack">
        <p className="muted measure">
          Reading a plant's data needs membership of its reader group. Groups are managed in the identity provider (or access.yaml in local
          development); Alchemist only reads them.
        </p>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Plant</th>
                <th scope="col">Reader group</th>
                <th scope="col">In the access model</th>
              </tr>
            </thead>
            <tbody>
              {plants
                ?.filter((p) => p.id !== "global")
                .map((p) => (
                  <tr key={p.id}>
                    <td>{plantTitle(p)}</td>
                    <td className="mono">{p.readers_group || "none"}</td>
                    <td>{p.in_access_model ? "Yes" : <span className="warn-text">No: nobody can read its data yet</span>}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        {meta?.auth === "dev" && (
          <>
            <h2 className="h-section">Users in access.yaml</h2>
            <ErrorNote error={error} />
            {users && (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th scope="col">User</th>
                      <th scope="col">Plants</th>
                      <th scope="col">Workspaces</th>
                      <th scope="col">Role</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.id}>
                        <td className="mono">{u.id}</td>
                        <td className="mono">{u.global || u.admin ? "all plants" : u.plants.join(", ")}</td>
                        <td>{u.workspaces.join(", ")}</td>
                        <td>{u.admin ? "admin" : u.global ? "reads all plants" : "member"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** The model for each role (admins change them; catalog admins see them) and the run limits from /meta. */
export function ModelsPage() {
  const { meta, me } = useSession();
  const { data, error, setData } = useApi<ModelSettings>("/admin/models");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const canEdit = !!me?.admin;
  const value = (r: ModelRole) => draft[r.role] ?? r.spec;
  const changed = data ? data.roles.filter((r) => value(r).trim() !== r.spec) : [];

  const save = async (models: Record<string, string | null>, done: string) => {
    setBusy(true);
    setSaveError(null);
    setMsg(null);
    try {
      setData(await api.put<ModelSettings>("/admin/models", { models }));
      setDraft({});
      setMsg(done);
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const models = Object.fromEntries(changed.map((r) => [r.role, value(r).trim() || null]));
    save(models, `Saved. New planning and analysis steps use these models; earlier runs keep the models they recorded.`);
  };

  if (!meta) return <Loading />;
  return (
    <div className="page">
      <Head title="Models and limits" />
      <div className="section stack measure-wide">
        <p className="muted measure">
          Each part of a run uses its own model. The deployment's configuration sets them; {canEdit ? "you" : "an admin"} can choose another one
          here. A model is a <span className="mono">provider:model-id</span>, and only providers allowed to receive plant data (
          <span className="mono">{meta.models.allowed_providers.join(", ")}</span>) are accepted.
        </p>
        {meta.models.scripted && <p className="warn-text small">This server plays the offline demo script, so runs do not call these models.</p>}
        <ErrorNote error={error} />
        {!data && !error && <Loading />}
        {data && (
          <form onSubmit={submit} className="stack">
            <div className="table-wrap">
              <table className="table edit-table">
                <thead>
                  <tr>
                    <th scope="col">Role</th>
                    <th scope="col">Model</th>
                    <th scope="col">Set by</th>
                  </tr>
                </thead>
                <tbody>
                  {data.roles.map((r) => (
                    <tr key={r.role}>
                      <td>
                        <div className="strong-ish">{r.label}</div>
                        <div className="muted small">{r.does}</div>
                      </td>
                      <td>
                        {canEdit ? (
                          <input
                            className="mono model-input"
                            aria-label={`${r.label} model`}
                            list="model-suggestions"
                            value={value(r)}
                            onChange={(e) => setDraft({ ...draft, [r.role]: e.target.value })}
                            spellCheck={false}
                          />
                        ) : (
                          <span className="mono">{r.spec}</span>
                        )}
                        {r.ignored && (
                          <div className="warn-text small">
                            The saved choice <span className="mono">{r.ignored.spec}</span> is ignored: its provider is no longer allowed.
                          </div>
                        )}
                      </td>
                      <td className="small">
                        {r.source === "admin" ? (
                          <>
                            <div>
                              {r.updated_by}, {day(r.updated_at)}
                            </div>
                            {canEdit && (
                              <button
                                type="button"
                                className="ghost small"
                                disabled={busy}
                                onClick={() => save({ [r.role]: null }, `${r.label} uses the configured model again.`)}
                              >
                                Use the configured {r.configured}
                              </button>
                            )}
                          </>
                        ) : (
                          <span className="muted">
                            configuration (<span className="mono">{r.configured_from === "default" ? "built-in default" : r.configured_from}</span>)
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <datalist id="model-suggestions">
                {[...new Set(data.roles.flatMap((r) => [r.default, r.configured]))].map((m) => (
                  <option key={m} value={m} />
                ))}
              </datalist>
            </div>
            <ErrorNote error={saveError} />
            {msg && (
              <p className="ok-text small" role="status">
                {msg}
              </p>
            )}
            {canEdit && (
              <div className="row-center">
                <button className="primary" disabled={busy || changed.length === 0}>
                  {busy ? "Saving…" : "Save models"}
                </button>
                {changed.length > 0 && (
                  <button type="button" className="ghost" onClick={() => setDraft({})}>
                    Undo changes
                  </button>
                )}
              </div>
            )}
            <p className="muted small measure">
              Each run records the models it used, so its report and replays stay tied to them after a change. On Bedrock, the deployment's
              role must also be allowed to call the model you pick.
            </p>
          </form>
        )}

        <h2 className="h-section">Limits</h2>
        <LimitsForm canEdit={canEdit} />
        <div className="table-wrap">
          <table className="table kv">
            <tbody>
              <tr>
                <th scope="row">Runs execute on</th>
                <td className="mono">{meta.launcher}</td>
              </tr>
              <tr>
                <th scope="row">Card drafters</th>
                <td className="mono">{meta.options.card_drafters.join(", ")}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/** Run limits: how long a run may work by default and at most, and the per-step budgets. Admins change them. */
function LimitsForm({ canEdit }: { canEdit: boolean }) {
  const { reload } = useSession();
  const { data, error, setData } = useApi<{ limits: RunLimit[] }>("/admin/limits");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const value = (l: RunLimit) => draft[l.name] ?? String(l.value);
  const changed = data ? data.limits.filter((l) => value(l).trim() !== String(l.value)) : [];

  const save = async (limits: Record<string, number | null>, done: string) => {
    setBusy(true);
    setSaveError(null);
    setMsg(null);
    try {
      setData(await api.put<{ limits: RunLimit[] }>("/admin/limits", { limits }));
      setDraft({});
      setMsg(done);
      reload(); // the limits people see when approving a plan
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    save(
      Object.fromEntries(changed.map((l) => [l.name, value(l).trim() ? Number(value(l)) : null])),
      "Saved. Plans approved from now on get these limits; runs already approved keep theirs.",
    );
  };

  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  return (
    <form onSubmit={submit} className="stack">
      <div className="table-wrap">
        <table className="table edit-table">
          <thead>
            <tr>
              <th scope="col">Limit</th>
              <th scope="col">Value</th>
              <th scope="col">Set by</th>
            </tr>
          </thead>
          <tbody>
            {data.limits.map((l) => (
              <tr key={l.name}>
                <td>
                  <div className="strong-ish">{l.label}</div>
                  <div className="muted small">{l.does}</div>
                </td>
                <td>
                  {canEdit ? (
                    <input
                      className="mono limit-input"
                      type="number"
                      min={l.minimum}
                      max={l.maximum}
                      aria-label={l.label}
                      value={value(l)}
                      onChange={(e) => setDraft({ ...draft, [l.name]: e.target.value })}
                    />
                  ) : (
                    <span className="mono">{l.value.toLocaleString()}</span>
                  )}
                </td>
                <td className="small">
                  {l.source === "admin" ? (
                    <>
                      <div>
                        {l.updated_by}, {day(l.updated_at)}
                      </div>
                      {canEdit && (
                        <button type="button" className="ghost small" disabled={busy} onClick={() => save({ [l.name]: null }, `${l.label} uses the configured value again.`)}>
                          Use the configured {l.configured.toLocaleString()}
                        </button>
                      )}
                    </>
                  ) : (
                    <span className="muted">
                      configuration (<span className="mono">{l.env}</span>)
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ErrorNote error={saveError} />
      {msg && (
        <p className="ok-text small" role="status">
          {msg}
        </p>
      )}
      {canEdit && (
        <div className="row-center">
          <button className="primary" disabled={busy || changed.length === 0}>
            {busy ? "Saving…" : "Save limits"}
          </button>
          {changed.length > 0 && (
            <button type="button" className="ghost" onClick={() => setDraft({})}>
              Undo changes
            </button>
          )}
        </div>
      )}
      <p className="muted small measure">
        The time limit counts the analysis's working time; time paused or waiting for a worker does not count. It is checked before each model
        call, so a long query or cell can run a little past it. A run that reaches a limit stops and seals a partial report of what it found.
      </p>
    </form>
  );
}
