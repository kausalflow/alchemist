import { useSession } from "../session";

/** What this deployment runs: models, budgets, agent tools and plugins, all read from /api/meta. */
export default function AboutPage() {
  const { meta, me } = useSession();
  if (!meta) return null;
  return (
    <div className="page">
      <header className="page-head">
        <div className="grow">
          <div className="crumb">Alchemist {meta.version}</div>
          <h1>About this deployment</h1>
        </div>
      </header>
      <div className="section stack measure-wide">
      <div className="table-wrap">
      <table className="table kv">
        <tbody>
          <tr>
            <th scope="row">Version</th>
            <td>{meta.version}</td>
          </tr>
          {(
            [
              ["Planning model", "planning"],
              ["Analysis model", "analysis"],
              ["Reviewer model", "reviewer"],
              ["Small-jobs model (catalog cards)", "small"],
            ] as const
          ).map(([label, role]) => (
            <tr key={role}>
              <th scope="row">{label}</th>
              <td>{meta.models.scripted ? "offline demo script" : meta.models.roles[role]}</td>
            </tr>
          ))}
          <tr>
            <th scope="row">Providers allowed to receive plant data</th>
            <td>{meta.models.allowed_providers.join(", ")}</td>
          </tr>
          <tr>
            <th scope="row">Budget per step</th>
            <td>
              {meta.budgets.max_steps} tool calls, {meta.budgets.max_output_tokens.toLocaleString()} output tokens
            </td>
          </tr>
          <tr>
            <th scope="row">Prompts and tools hash</th>
            <td>
              <code>{meta.prompts}</code>
            </td>
          </tr>
          <tr>
            <th scope="row">Sign-in</th>
            <td>{meta.auth === "dev" ? "local development (pick a user)" : "corporate identity provider"}</td>
          </tr>
          <tr>
            <th scope="row">Runs execute on</th>
            <td>{meta.launcher}</td>
          </tr>
          <tr>
            <th scope="row">Plugins</th>
            <td>{meta.plugins.length ? meta.plugins.map((p) => (typeof p === "string" ? p : `${p.name}${p.version ? ` ${p.version}` : ""}`)).join(", ") : "none"}</td>
          </tr>
          <tr>
            <th scope="row">You</th>
            <td>
              {me?.id} · plants {me?.readable_plants.join(", ")} · workspaces {me?.workspaces.join(", ")}
              {me?.admin && " · admin"}
              {me?.catalog_admin && " · catalog admin"}
            </td>
          </tr>
        </tbody>
      </table>
      </div>
      <h2 className="h-section">Agent tools</h2>
      <p className="muted small">The only things the agent can do. Planning tools cannot read data; analysis tools run in the sandbox.</p>
      {(["planning", "analysis"] as const).map((phase) => (
        <div key={phase}>
          <h3>{phase}</h3>
          <ul>
            {meta.tools[phase].map((t) => (
              <li key={t.name}>
                <code>{t.name}</code> <span className="small">{t.description}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <p className="small">
        API reference: <a href="/api/docs">/api/docs</a> · OpenAPI: <a href="/api/openapi.json">/api/openapi.json</a>
      </p>
      </div>
    </div>
  );
}
