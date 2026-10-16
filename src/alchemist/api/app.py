"""The Alchemist REST API and, when built, the web app.

    alchemist serve                    API on http://127.0.0.1:8000/api, web app on http://127.0.0.1:8000/
    uvicorn alchemist.api.app:app      the same, for a container

Extending it: the launcher and identity are swappable without touching this file, and other packages can add routes.

    ALC_LAUNCHER=package.module:factory   factory(services_factory) -> a Launcher (see jobs.py); default LocalLauncher
    entry point group "alchemist.api"     each entry point is an APIRouter (or a callable returning one), mounted
                                          under /api/ext/<entry point name>

The web app and CLI use only this API; the OpenAPI spec is at /api/openapi.json.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import __version__, model_roles, prompts
from ..access import AccessDenied
from ..config import Settings, load_settings
from ..paths import as_path, read_yaml
from . import admin as admin_api
from . import catalog as catalog_api
from . import runs as runs_api
from .deps import Me, Svc, in_workspace
from .identity import auth_mode
from .jobs import LocalLauncher

log = logging.getLogger("alchemist.api")
ENTRY_POINT_GROUP = "alchemist.api"


def load_object(spec: str):
    """Import `package.module:attribute`."""
    module, _, attr = spec.partition(":")
    obj = importlib.import_module(module)
    for part in attr.split(".") if attr else []:
        obj = getattr(obj, part)
    return obj


def services_factory_for(settings: Settings, ttl: float | None = None):
    """Services per request or step. The database connection is always new (one per thread); the access model,
    knowledge index and credential broker are shared for `ttl` seconds (ALC_SERVICES_TTL, default 60), since
    building them reads access.yaml and every knowledge document from S3."""
    import threading
    import time

    from ..run import Services

    ttl = float(os.environ.get("ALC_SERVICES_TTL", "60")) if ttl is None else ttl
    cache: dict = {}
    lock = threading.Lock()

    def factory():
        if ttl <= 0:
            return Services(settings)
        with lock:
            if not cache or time.monotonic() - cache["at"] > ttl:
                svc = Services(settings)
                cache.update(at=time.monotonic(), access=svc.access, knowledge=svc.knowledge, broker=svc.broker)
                return svc
            return Services(settings, access=cache["access"], knowledge=cache["knowledge"], broker=cache["broker"])

    def invalidate():
        """Rebuild the shared services on the next call (the admin area calls this after editing knowledge)."""
        with lock:
            cache.clear()

    factory.invalidate = invalidate
    return factory


def plugin_routers() -> list[tuple[str, APIRouter]]:
    out = []
    for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        try:
            obj = ep.load()
            router = obj if isinstance(obj, APIRouter) else obj()
            out.append((ep.name, router))
        except Exception:  # a broken plugin must not take the API down
            log.exception("could not load API plugin %s", ep.name)
    return out


core = APIRouter(tags=["session"])


def _known_workspaces(svc) -> set[str]:
    """Workspaces the access model defines (access.yaml); with identity-provider groups, members' own list adds more."""
    return set(getattr(svc.access, "data", {}).get("workspaces", {}) or {})


@core.get("/me")
def me(svc: Svc, me: Me) -> dict:
    u = me.user
    return {"id": u.id, "name": me.name or u.id, "email": me.email, "plants": sorted(u.plants),
            "global": u.is_global, "admin": u.is_admin, "catalog_admin": me.is_catalog_admin,
            "workspaces": sorted(_known_workspaces(svc) if u.is_admin else u.workspaces),
            "readable_plants": sorted(svc.access.all_plants if (u.is_global or u.is_admin) else u.plants)}


@core.get("/workspaces")
def workspaces(svc: Svc, me: Me) -> list[dict]:
    """Workspaces the user belongs to, with the plants each covers and its projects."""
    root = as_path(svc.settings.workspace_root) / "workspaces"
    out = []
    for name in sorted(_known_workspaces(svc) | set(me.user.workspaces)):
        if not in_workspace(me, name):
            continue
        try:
            plants = sorted(svc.access.workspace_plants(name))
        except AccessDenied:
            continue
        projects = []
        pdir = root / name / "projects"
        if pdir.exists():
            for p in sorted(pdir.iterdir(), key=str):
                if (p / "project.yaml").exists():
                    meta = read_yaml(p / "project.yaml")
                    projects.append({"name": meta.get("name", p.name), "owner": meta.get("owner"),
                                     "plants": meta.get("plants", []), "created_at": meta.get("created_at")})
        out.append({"name": name, "plants": plants, "projects": projects})
    return out


@core.get("/meta")
def meta(request: Request, svc: Svc, me: Me) -> dict:
    """What this deployment offers: models, agent tools, auth mode and loaded plugins. The web app reads this
    rather than hard-coding lists, so an added tool or plugin shows up without a web release."""
    s: Settings = request.app.state.settings
    return {
        "version": __version__,
        "auth": auth_mode(),
        "models": {"roles": {r["role"]: r["spec"] for r in model_roles.describe(s, svc.catalog.db)},
                   "allowed_providers": s.allowed_providers,
                   "scripted": os.environ.get("ALC_API_MODEL") == "scripted"},
        "budgets": {"max_steps": s.max_steps, "max_output_tokens": s.max_output_tokens_total},
        "tools": {"planning": [_tool(n) for n in prompts.PLAN_TOOLS] + _plugin_tools("plan"),
                  "analysis": [_tool(n) for n in prompts.ANALYSIS_TOOLS] + _plugin_tools("analysis")},
        "prompts": prompts.prompt_version(),
        "plugins": _registry_summary() + [{"name": n, "kind": "api"} for n in request.app.state.plugins],
        "options": _registry_options(),
        "launcher": type(request.app.state.launcher).__name__,
    }


def _registry():
    """The plugin registry (alchemist.plugins), when this build has it."""
    try:
        from ..plugins import get_registry
    except ImportError:
        return None
    return get_registry()


def _registry_summary() -> list[dict]:
    r = _registry()
    return list(r.summary()) if r else []


def _plugin_tools(phase: str) -> list[dict]:
    r = _registry()
    return [{"name": t.name, "description": t.description.split("\n")[0], "plugin": True}
            for t in r.tools_for(phase)] if r else []


def _registry_options() -> dict:
    """Choices the web app offers in forms (dataset formats, card drafters), from the registry when present."""
    r = _registry()
    if not r:
        return {"data_formats": ["parquet", "csv", "json"], "card_drafters": ["heuristic", "model"]}
    return {"data_formats": r.data_formats.names(), "card_drafters": r.card_drafters.names()}


def _tool(name: str) -> dict:
    t = prompts.TOOLS[name]
    return {"name": name, "description": t.get("description", "").split("\n")[0]}


@core.get("/dev/users")
def dev_users(svc: Svc) -> list[dict]:
    """Dev mode only: the users in access.yaml, for the web app's sign-in picker."""
    if auth_mode() != "dev":
        raise HTTPException(404)
    return [{"id": k, "plants": v.get("plants", []), "workspaces": v.get("workspaces", []),
             "admin": bool(v.get("admin")), "global": bool(v.get("global"))}
            for k, v in getattr(svc.access, "data", {}).get("users", {}).items()]


def create_app(settings: Settings | None = None, *, launcher=None, services_factory=None,
               web_dist: str | os.PathLike | None = None) -> FastAPI:
    settings = settings or load_settings()
    services_factory = services_factory or services_factory_for(settings)
    if launcher is None:
        spec = os.environ.get("ALC_LAUNCHER")
        launcher = load_object(spec)(services_factory) if spec else LocalLauncher(services_factory)

    every = float(os.environ.get("ALC_RECONCILE_SECONDS", "60"))

    async def reconcile_forever():
        while True:
            try:
                await asyncio.to_thread(launcher.reconcile)
            except Exception:
                log.exception("reconciling runs failed")
            await asyncio.sleep(every)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(reconcile_forever()) if every > 0 and hasattr(launcher, "reconcile") else None
        yield
        if task:
            task.cancel()
        if hasattr(launcher, "shutdown"):
            launcher.shutdown()

    app = FastAPI(title="Alchemist", version=__version__, lifespan=lifespan,
                  description="AI research system for chemical engineering process questions.",
                  openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url=None)
    app.state.settings = settings
    app.state.services_factory = services_factory
    app.state.launcher = launcher
    app.state.plugins = []

    for r in (core, runs_api.router, catalog_api.router, admin_api.router):
        app.include_router(r, prefix="/api")
    for name, r in plugin_routers():
        app.include_router(r, prefix=f"/api/ext/{name}")
        app.state.plugins.append(name)

    @app.get("/api/health", include_in_schema=False)
    @app.get("/healthz", include_in_schema=False)  # load balancer health check
    def health():
        return {"ok": True}

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)

    dist = Path(web_dist or os.environ.get("ALC_WEB_DIST") or Path(__file__).resolve().parents[3] / "web" / "dist")
    if (dist / "index.html").exists():
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/") or path == "healthz":
                raise HTTPException(404)
            f = (dist / path).resolve()
            if path and f.is_file() and dist.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(index)  # client-side routes
    return app


def __getattr__(name: str):
    # `uvicorn alchemist.api.app:app` builds the app from the environment on first use.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
