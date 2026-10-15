import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { Workspace } from "./api";
import { useApi } from "./session";

// The workspace the rail switcher has selected. The board, ask page and reports start from it.

interface WorkspaceCtx {
  workspaces: Workspace[] | null;
  current: string;
  ws: Workspace | undefined;
  setCurrent: (name: string) => void;
}

const KEY = "alchemist.workspace";
const Ctx = createContext<WorkspaceCtx | null>(null);

function stored(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { data: workspaces } = useApi<Workspace[]>("/workspaces");
  const [current, setCurrentState] = useState(stored);

  useEffect(() => {
    if (workspaces?.length && !workspaces.some((w) => w.name === current)) setCurrentState(workspaces[0].name);
  }, [workspaces, current]);

  const setCurrent = (name: string) => {
    setCurrentState(name);
    try {
      localStorage.setItem(KEY, name);
    } catch {
      /* storage blocked: the choice lasts for this page only */
    }
  };

  const ws = workspaces?.find((w) => w.name === current);
  return <Ctx.Provider value={{ workspaces, current, ws, setCurrent }}>{children}</Ctx.Provider>;
}

export function useWorkspace(): WorkspaceCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useWorkspace outside WorkspaceProvider");
  return c;
}
