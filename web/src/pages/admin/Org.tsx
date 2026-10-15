import type { DevUser, Workspace } from "../../api";
import { useApi, useSession } from "../../session";
import { ErrorNote, Loading } from "../../components/bits";
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

/** Models and run limits from /meta (read-only: set in the deployment's configuration). */
export function ModelsPage() {
  const { meta } = useSession();
  if (!meta) return <Loading />;
  return (
    <div className="page">
      <Head title="Models and limits" />
      <div className="section stack measure-wide">
        <p className="muted measure">The model provider, models and limits are set in the deployment's configuration and shown here read-only.</p>
        <div className="table-wrap">
          <table className="table kv">
            <tbody>
              <tr>
                <th scope="row">Lead model</th>
                <td className="mono">{meta.models.scripted ? "offline demo script" : meta.models.lead}</td>
              </tr>
              <tr>
                <th scope="row">Reviewer and card drafter</th>
                <td className="mono">{meta.models.scripted ? "offline demo script" : meta.models.sub}</td>
              </tr>
              <tr>
                <th scope="row">Providers allowed to receive plant data</th>
                <td className="mono">{meta.models.allowed_providers.join(", ")}</td>
              </tr>
              <tr>
                <th scope="row">Tool calls per run step</th>
                <td className="mono">{meta.budgets.max_steps}</td>
              </tr>
              <tr>
                <th scope="row">Output tokens per run step</th>
                <td className="mono">{meta.budgets.max_output_tokens.toLocaleString()}</td>
              </tr>
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
