import { useEffect, useState } from "react";
import { api, type DevUser } from "../api";
import { useSession } from "../session";
import Icon from "../components/icons";

/** Dev mode: pick a user from access.yaml. With corporate sign-in the load balancer handles this page. */
export default function SignIn() {
  const { signInAs } = useSession();
  const [users, setUsers] = useState<DevUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<DevUser[]>("/dev/users")
      .then(setUsers)
      .catch(() => setError("You are not signed in. Sign in through your company's identity provider and reload."));
  }, []);

  return (
    <div className="signin">
      <h1 className="brand-title">
        <Icon name="brand" size={30} /> Alchemist
      </h1>
      <p className="muted">Research answers to process questions, with the data, methods and code behind them.</p>
      {error && <p className="error">{error}</p>}
      {users && (
        <>
          <h2>Sign in as (local development)</h2>
          <p className="muted small">Users come from access.yaml. In production the corporate identity provider signs you in.</p>
          <ul className="userlist">
            {users.map((u) => (
              <li key={u.id}>
                <button type="button" onClick={() => signInAs(u.id)}>
                  <strong>{u.id}</strong>
                  <span className="muted small">
                    {u.admin ? "admin · " : ""}
                    {u.global || u.admin ? "all plants" : `plants ${u.plants.join(", ")}`} · workspaces {u.workspaces.join(", ")}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
