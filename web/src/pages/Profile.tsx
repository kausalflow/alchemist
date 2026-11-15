import { useSession } from "../session";

/** Who you are signed in as and why you can see what you see: the groups your company sign-in sent and what each one
 * gives here, so people know what to ask IT for. */
export default function ProfilePage() {
  const { me, meta } = useSession();
  const u = me!;
  const corporate = u.auth === "alb-oidc" || u.auth === "oidc";
  const plants = u.global || u.admin ? "every plant" : u.readable_plants.join(", ") || "no plant yet";
  const used = (u.groups ?? []).filter((g) => g.used);
  const unused = (u.groups ?? []).filter((g) => !g.used);
  const names = u.group_names ?? {};
  const example = (key: string, value: string) => (names[key] ? names[key].replace(/\{[a-z]+\}/, value) : null);

  return (
    <div className="page">
      <header className="page-head">
        <div className="grow">
          <div className="crumb">Your profile</div>
          <h1>{u.name}</h1>
          {u.email && <div className="muted">{u.email}</div>}
        </div>
      </header>
      <div className="section stack-lg measure-wide">
        <section className="panel pad stack-sm" aria-labelledby="signin">
          <h2 id="signin" className="h-panel">
            How you signed in
          </h2>
          <p>
            {corporate
              ? "With your company account. The sign-in page in front of Alchemist checked who you are and sent your name, email and groups."
              : "As a local test user picked from access.yaml. On the hosted version you sign in with your company account instead."}
          </p>
          <dl className="kv small">
            <dt>User id</dt>
            <dd className="mono">{u.id}</dd>
            {u.email && (
              <>
                <dt>Email</dt>
                <dd>{u.email}</dd>
              </>
            )}
            <dt>Sign-in</dt>
            <dd>{corporate ? (u.auth === "alb-oidc" ? "Company sign-in at the load balancer" : "Company sign-in") : "Local test users"}</dd>
            {meta?.version && (
              <>
                <dt>Alchemist</dt>
                <dd className="mono">{meta.version}</dd>
              </>
            )}
          </dl>
        </section>

        <section className="panel pad stack-sm" aria-labelledby="can">
          <h2 id="can" className="h-panel">
            What you can do
          </h2>
          <ul className="bullets">
            <li>Read data from {plants}</li>
            <li>
              Ask questions in {u.workspaces.length ? (u.workspaces.length === 1 ? "workspace " : "workspaces ") : "no workspace yet"}
              {u.workspaces.join(", ")}
            </li>
            {u.admin && <li>Use the admin pages: models, workspaces, access and each plant's data and knowledge</li>}
            {!u.admin && u.catalog_admin && <li>Approve data sources and plant knowledge (catalog admin)</li>}
          </ul>
        </section>

        {corporate && (
          <section className="panel pad stack-sm" aria-labelledby="groups">
            <h2 id="groups" className="h-panel">
              Your groups
            </h2>
            {used.length === 0 && <p className="muted">Your sign-in sent no group that Alchemist uses.</p>}
            {used.length > 0 && (
              <table className="table small">
                <thead>
                  <tr>
                    <th scope="col">Group</th>
                    <th scope="col">Gives you</th>
                  </tr>
                </thead>
                <tbody>
                  {used.map((g) => (
                    <tr key={g.id ?? g.group}>
                      <td className="mono">{g.group}</td>
                      <td>{g.means}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {unused.length > 0 && (
              <details className="small">
                <summary>
                  {unused.length} other {unused.length === 1 ? "group" : "groups"} Alchemist does not use
                </summary>
                <p className="mono faint">{unused.map((g) => g.group).join(", ")}</p>
              </details>
            )}
          </section>
        )}

        <section className="panel pad stack-sm" aria-labelledby="more">
          <h2 id="more" className="h-panel">
            Need more access?
          </h2>
          {corporate ? (
            <>
              <p className="small">Access comes from your company groups. Ask IT to add you to the group you need, then sign in again:</p>
              <ul className="bullets small">
                {example("plant_group", "P01") && (
                  <li>
                    <span className="mono">{example("plant_group", "<plant>")}</span> to read a plant's data, for example{" "}
                    <span className="mono">{example("plant_group", "P01")}</span>
                  </li>
                )}
                {example("workspace_group", "quality") && (
                  <li>
                    <span className="mono">{example("workspace_group", "<workspace>")}</span> to ask questions in a workspace
                  </li>
                )}
              </ul>
            </>
          ) : (
            <p className="small">Local test users and their plants and workspaces are set in access.yaml.</p>
          )}
        </section>
      </div>
    </div>
  );
}
