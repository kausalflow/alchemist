"""Access model: plant groups decide data, workspaces decide projects.

In production the groups come from the identity provider and enforcement happens
at S3 through per-run credentials. The MVP reads groups from access.yaml and
enforces them in the tool layer, using the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from .paths import as_path, read_yaml


class AccessDenied(Exception):
    pass


@dataclass(frozen=True)
class User:
    id: str
    plants: frozenset[str]
    is_global: bool
    is_admin: bool
    workspaces: frozenset[str]


class AccessModel:
    def __init__(self, data: dict):
        self.data = data or {}
        self.all_plants = frozenset(self.data.get("plants", []))

    @classmethod
    def load(cls, path) -> "AccessModel":
        p = as_path(path)
        return cls(read_yaml(p) if p.exists() else {})

    def user(self, user_id: str) -> User:
        u = self.data.get("users", {}).get(user_id)
        if u is None:
            raise AccessDenied(f"unknown user {user_id!r}")
        return User(
            id=user_id,
            plants=frozenset(u.get("plants", [])),
            is_global=bool(u.get("global", False)),
            is_admin=bool(u.get("admin", False)),
            workspaces=frozenset(u.get("workspaces", [])),
        )

    def workspace_plants(self, workspace: str) -> frozenset[str]:
        ws = self.data.get("workspaces", {}).get(workspace)
        if ws is None:
            raise AccessDenied(f"unknown workspace {workspace!r}")
        return frozenset(ws.get("plants", [])) or self.all_plants

    def run_plants(self, user: User, workspace: str, scope_plants: list[str] | None = None) -> frozenset[str]:
        """Plants a run may read: the user's plants, within the workspace, within the question's scope."""
        if not (user.is_admin or workspace in user.workspaces):
            raise AccessDenied(f"{user.id} is not a member of workspace {workspace!r}")
        mine = self.all_plants if (user.is_global or user.is_admin) else user.plants
        allowed = mine & self.workspace_plants(workspace)
        if scope_plants:
            missing = set(scope_plants) - allowed
            if missing:
                raise AccessDenied(f"{user.id} may not read plants {sorted(missing)} in workspace {workspace!r}")
            allowed = allowed & frozenset(scope_plants)
        if not allowed:
            raise AccessDenied(f"{user.id} has no readable plants in workspace {workspace!r}")
        return allowed

    def can_view_run(self, user: User, workspace: str, run_plants: list[str]) -> bool:
        if user.is_admin:
            return True
        if workspace not in user.workspaces:
            return False
        mine = self.all_plants if user.is_global else user.plants
        return set(run_plants) <= mine
