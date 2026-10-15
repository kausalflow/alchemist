import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, type Me, type Meta } from "./api";

interface Session {
  me: Me | null;
  meta: Meta | null;
  loading: boolean;
  signedOut: boolean;
  reload: () => void;
  signInAs: (user: string) => void; // dev mode only
  signOut: () => void;
}

const Ctx = createContext<Session | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [loading, setLoading] = useState(true);
  const [signedOut, setSignedOut] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    Promise.all([api.get<Me>("/me"), api.get<Meta>("/meta")])
      .then(([m, mt]) => {
        setMe(m);
        setMeta(mt);
        setSignedOut(false);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) setSignedOut(true);
        setMe(null);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(reload, [reload]);

  const signInAs = (user: string) => {
    document.cookie = `alc_user=${encodeURIComponent(user)}; path=/; SameSite=Strict`;
    reload();
  };
  const signOut = () => {
    document.cookie = "alc_user=; path=/; max-age=0";
    setMe(null);
    setSignedOut(true);
  };

  return <Ctx.Provider value={{ me, meta, loading, signedOut, reload, signInAs, signOut }}>{children}</Ctx.Provider>;
}

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

/** Load JSON from the API; re-runs when `path` changes. Pass null to skip. */
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setError(null);
    api
      .get<T>(path)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(e.message));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  return { data, error, reload: () => setTick((t) => t + 1), setData };
}
