"""Access model: plant groups decide data, workspaces decide projects.

Who is in which group comes from the identity provider: the API verifies the
user's OIDC token and passes its claims to `AccessModel.user_from_claims`,
which maps group names to plants, workspaces and roles using the `identity`
section of access.yaml. The same file lists the plants and workspaces, and may
list local users, which the CLI, the demo and tests use instead of an identity
provider.

A run's plants are enforced twice: in the tool layer (views and the SQL guard),
and at S3 through the run's scoped credentials (see credentials.py).
"""

from __future__ import annotations

import re
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
    is_catalog_admin: bool = False
    source: str = "file"  # "file" (access.yaml) or "oidc" (identity-provider claims)


# Default group names; `{plant}` and `{workspace}` mark the variable part.
DEFAULT_IDENTITY = {
    "group_claim": "groups",
    "user_claims": ["preferred_username", "email", "sub"],
    "plant_group": "alc-plant-{plant}",
    "workspace_group": "alc-ws-{workspace}",
    "global_group": "alc-global",
    "admin_group": "alc-admin",
    "catalog_admin_group": "alc-catalog-admin",
    "group_aliases": {},
}


def _pattern(template: str, name: str) -> re.Pattern:
    head, _, tail = template.partition("{" + name + "}")
    return re.compile("^" + re.escape(head) + "(?P<v>.+?)" + re.escape(tail) + "$")


class AccessModel:
    def __init__(self, data: dict):
        self.data = data or {}
        self.all_plants = frozenset(self.data.get("plants", []))
        self.identity = {**DEFAULT_IDENTITY, **(self.data.get("identity") or {})}

    @classmethod
    def load(cls, path) -> "AccessModel":
        p = as_path(path)
        return cls(read_yaml(p) if p.exists() else {})

    # ---------- who the user is ----------
    def user(self, user_id: str) -> User:
        """A local user from access.yaml (CLI, demo, tests)."""
        u = self.data.get("users", {}).get(user_id)
        if u is None:
            raise AccessDenied(f"unknown user {user_id!r}")
        return User(
            id=user_id,
            plants=frozenset(u.get("plants", [])),
            is_global=bool(u.get("global", False)),
            is_admin=bool(u.get("admin", False)),
            workspaces=frozenset(u.get("workspaces", [])),
            is_catalog_admin=bool(u.get("catalog_admin", False)),
        )

    def user_from_claims(self, claims: dict) -> User:
        """A user from the verified claims of an identity-provider token.

        Group names map to roles through the `identity` section of access.yaml.
        Providers that send group ids instead of names (Entra ID) map them with
        `group_aliases: {<group id>: alc-plant-P01}`. Groups for plants or
        workspaces this deployment does not define are ignored.
        """
        idc = self.identity
        user_id = next((str(claims[c]) for c in idc["user_claims"] if claims.get(c)), None)
        if not user_id:
            raise AccessDenied(f"token has none of the user claims {idc['user_claims']}")
        raw = claims.get(idc["group_claim"], [])
        if isinstance(raw, str):
            raw = re.split(r"[,\s]+", raw.strip())
        aliases = idc.get("group_aliases") or {}
        groups = {aliases.get(g, g) for g in raw if g}

        plant_re, ws_re = _pattern(idc["plant_group"], "plant"), _pattern(idc["workspace_group"], "workspace")
        known_ws = set(self.data.get("workspaces", {}))
        plants, workspaces = set(), set()
        for g in groups:
            if m := plant_re.match(g):
                if not self.all_plants or m["v"] in self.all_plants:
                    plants.add(m["v"])
            elif m := ws_re.match(g):
                if m["v"] in known_ws:
                    workspaces.add(m["v"])
        return User(id=user_id, plants=frozenset(plants), is_global=idc["global_group"] in groups,
                    is_admin=idc["admin_group"] in groups, workspaces=frozenset(workspaces),
                    is_catalog_admin=idc["catalog_admin_group"] in groups, source="oidc")

    def resolve(self, user: "str | User") -> User:
        """Accept a User built from claims, or a local user id."""
        return user if isinstance(user, User) else self.user(user)

    # ---------- what they may do ----------
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
