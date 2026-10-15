"""Plugins: how other packages extend Alchemist without editing it.

A plugin is an installed Python package that declares an entry point in the
`alchemist.plugins` group. The entry point names a `register(registry)`
function (or a module that has one), which adds contributions to typed
registries:

    [project.entry-points."alchemist.plugins"]
    acme = "acme_alchemist:register"

    def register(r: alchemist.plugins.Registry) -> None:
        r.data_formats.add(DataFormat("delta", reader="delta_scan"))
        r.tools.add(Tool(name="lookup_batch", ...))
        r.analysis_methods.add("cusum", cusum)

Extension points (attributes of `Registry`):

    data_formats       file formats a dataset can be registered with (DuckDB reader per format)
    storage_backends   URI schemes the sandbox's DuckDB can read (extensions, credentials)
    metadata_parsers   readers for dataset metadata files, chosen by file suffix
    card_drafters      ways to turn a metadata file plus a data sample into a catalog card
    model_providers    `provider:` prefixes that build a Pydantic AI model
    access_providers   where users, plant groups and workspaces come from
    tools              extra agent tools, for the planning and/or analysis step
    analysis_methods   functions the analysis code can call in the kernel as alc.methods.<name>
    knowledge_packs    folders of domain documents and structured knowledge
    report_renderers   extra report outputs written into the run folder before sealing
    cli_commands       extra `alchemist <command>` subcommands

Alchemist's own defaults are registered the same way (plugin "builtin"), so a
plugin can add a new choice or, with `replace=True`, swap a default.

Which installed plugins load is set by ALC_PLUGINS (comma-separated names, `*`
for all installed, `none` for only the built-ins). ALC_PLUGIN_MODULES loads
plugin modules that are importable but not packaged with an entry point
(`site_plugins.acme` or `site_plugins.acme:register`), handy for a site's own
code and for development. Every run records the loaded
plugins and their versions in its manifest, and plugin tool definitions are
part of the prompt hash, so a replay can tell when the plugin set changed.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generic, Iterator, Protocol, TypeVar

ENTRY_POINT_GROUP = "alchemist.plugins"
API_VERSION = 1

T = TypeVar("T")


class PluginError(Exception):
    pass


# ---------------------------------------------------------------- contribution types

@dataclass(frozen=True)
class DataFormat:
    """A dataset file format. `reader` is the DuckDB table function that reads a list of files."""
    name: str
    reader: str
    options: str = ""  # extra reader arguments, e.g. "union_by_name = true"
    description: str = ""

    def scan(self, files: list) -> str:
        file_list = ", ".join(f"'{f}'" for f in files)
        opts = f", {self.options}" if self.options else ""
        return f"{self.reader}([{file_list}]{opts})"


@dataclass(frozen=True)
class StorageBackend:
    """A URI scheme the sandbox's DuckDB connection can read, and how to set it up.

    `setup(con)` runs on the kernel's DuckDB connection before it is locked, once per run that reads a dataset
    under this scheme (install/load extensions, create secrets from the ambient credentials). It runs in the
    sandbox, which holds only the run's data credentials.
    """
    scheme: str  # "s3", "gs", "az", "file"
    setup: Callable[[Any], None]
    description: str = ""


@dataclass(frozen=True)
class MetadataParser:
    """Reads a dataset's own metadata file into Python data (dicts and lists) for card drafting."""
    name: str
    suffixes: tuple[str, ...]  # lower-case, with the dot: (".xml",)
    parse: Callable[[str], Any]
    description: str = ""


@dataclass
class DraftRequest:
    """What a card drafter gets: the dataset, its parsed and raw metadata, and a data sample."""
    dataset_id: str
    metadata: Any
    metadata_text: str
    sample: Any  # pandas DataFrame or None
    sample_columns: dict[str, str]  # column -> DuckDB type
    settings: Any


@dataclass(frozen=True)
class CardDrafter:
    """Turns a DraftRequest into an `alchemist.card/v1` card. Admins still review every draft."""
    name: str
    draft: Callable[[DraftRequest], dict]
    description: str = ""
    drafted_by: Callable[[Any], str] | None = None  # settings -> label stored with the draft; default: name


@dataclass(frozen=True)
class ModelProvider:
    """A `provider:` prefix in model specs. `build(model_id, settings)` returns a Pydantic AI Model.

    The provider allow-list (ALC_ALLOWED_PROVIDERS) applies to plugin providers too: a provider only gets plant
    data once an operator lists it.
    """
    name: str
    build: Callable[[str, Any], Any]
    description: str = ""


@dataclass(frozen=True)
class AccessProvider:
    """Where users, plant groups and workspaces come from. `load(settings)` returns an object with the
    interface of `alchemist.access.AccessModel`; subclass it and override `user`, `workspace_plants` and
    `all_plants` to keep Alchemist's rules (run plants = user plants within workspace within scope)."""
    name: str
    load: Callable[[Any], Any]
    description: str = ""


class ToolContext(Protocol):
    """What a plugin tool handler can use from the run (alchemist.run.RunContext)."""
    plants: frozenset[str]
    user_id: str
    run: Any  # run folder path

    def cite(self, doc: str, version: str, title: str, section: str, stale: bool = False) -> None: ...


@dataclass(frozen=True)
class Tool:
    """An agent tool. `handler(ctx, input)` returns text, or an `alchemist.agent.ToolOutput`.

    Handlers run in the orchestrator, which may call the model but must not read lake rows; code that needs
    data belongs in an analysis method, which runs in the sandbox kernel.
    """
    name: str
    description: str
    input_schema: dict
    handler: Callable[[Any, dict], Any]
    phases: tuple[str, ...] = ("analysis",)  # "plan" and/or "analysis"

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass(frozen=True)
class KnowledgePack:
    """A folder of domain knowledge laid out like ALC_KNOWLEDGE: `*.md` documents with front matter and
    `structured/*.yaml`."""
    name: str
    root: str | Path
    version: str = "1"
    description: str = ""


@dataclass(frozen=True)
class ReportRenderer:
    """An extra report output. `render(run_dir, report)` writes files under run_dir/report and returns their
    paths relative to the run folder. `report` holds the filled conclusion, findings, queries and context."""
    name: str
    render: Callable[[Any, dict], list[str]]
    description: str = ""


@dataclass(frozen=True)
class CliCommand:
    """An `alchemist <name>` subcommand: `add_arguments(parser)` then `run(args)`."""
    name: str
    help: str
    run: Callable[[Any], None]
    add_arguments: Callable[[Any], None] | None = None


# ---------------------------------------------------------------- registries

class Kind(Generic[T]):
    """A named collection of one kind of contribution."""

    def __init__(self, kind: str, owner: "Registry"):
        self.kind, self._owner = kind, owner
        self._items: dict[str, T] = {}
        self._from: dict[str, str] = {}

    def add(self, name_or_item, item: T | None = None, *, replace: bool = False) -> T:
        """`add(item)` keys by item.name (or .scheme); `add("name", obj)` for plain callables."""
        if item is None:
            item = name_or_item
            name = getattr(item, "name", None) or getattr(item, "scheme", None)
        else:
            name = name_or_item
        if not name:
            raise PluginError(f"{self.kind}: contribution needs a name")
        plugin = self._owner._current or "builtin"
        if name in self._items and not replace:
            raise PluginError(f"{self.kind} {name!r} from plugin {plugin!r} is already registered by plugin "
                              f"{self._from[name]!r}; pass replace=True to override it deliberately")
        self._items[name] = item
        self._from[name] = plugin
        return item

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            raise PluginError(f"no {self.kind} named {name!r}; available: {sorted(self._items)}") from None

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[T]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    def names(self) -> list[str]:
        return sorted(self._items)

    def items(self):
        return self._items.items()

    def provided_by(self, name: str) -> str:
        return self._from[name]


@dataclass
class PluginInfo:
    name: str
    module: str
    distribution: str | None = None
    version: str | None = None
    contributions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"name": self.name, "module": self.module, "distribution": self.distribution, "version": self.version,
                "contributions": self.contributions}


KINDS = ("data_formats", "storage_backends", "metadata_parsers", "card_drafters", "model_providers",
         "access_providers", "tools", "analysis_methods", "knowledge_packs", "report_renderers", "cli_commands")


class Registry:
    """All extension points. Use `get_registry()` for the process-wide one."""

    def __init__(self, *, builtins: bool = True):
        self._current: str | None = None
        self.plugins: dict[str, PluginInfo] = {}
        self.data_formats: Kind[DataFormat] = Kind("data format", self)
        self.storage_backends: Kind[StorageBackend] = Kind("storage backend", self)
        self.metadata_parsers: Kind[MetadataParser] = Kind("metadata parser", self)
        self.card_drafters: Kind[CardDrafter] = Kind("card drafter", self)
        self.model_providers: Kind[ModelProvider] = Kind("model provider", self)
        self.access_providers: Kind[AccessProvider] = Kind("access provider", self)
        self.tools: Kind[Tool] = Kind("tool", self)
        self.analysis_methods: Kind[Callable] = Kind("analysis method", self)
        self.knowledge_packs: Kind[KnowledgePack] = Kind("knowledge pack", self)
        self.report_renderers: Kind[ReportRenderer] = Kind("report renderer", self)
        self.cli_commands: Kind[CliCommand] = Kind("CLI command", self)
        if builtins:
            self.register_plugin("builtin", _register_builtins, module="alchemist.plugins")

    # ---------- loading ----------
    def register_plugin(self, name: str, register: Callable[["Registry"], None], *, module: str = "",
                        distribution: str | None = None, version: str | None = None) -> PluginInfo:
        if name in self.plugins:
            raise PluginError(f"plugin {name!r} is already loaded")
        before = {k: set(getattr(self, k).names()) for k in KINDS}
        self._current = name
        try:
            register(self)
        finally:
            self._current = None
        contributions = []
        for k in KINDS:
            kind = getattr(self, k)
            mine = [n for n in kind.names() if kind.provided_by(n) == name]
            contributions += [f"{k}:{n}" + (" (replaced)" if n in before[k] else "") for n in mine]
        info = PluginInfo(name=name, module=module, distribution=distribution, version=version,
                          contributions=contributions)
        self.plugins[name] = info
        return info

    def load_module(self, module: str, name: str | None = None) -> PluginInfo:
        """Load a plugin from `package.module` or `package.module:function` without an entry point."""
        mod_name, _, attr = module.partition(":")
        obj = importlib.import_module(mod_name)
        fn = getattr(obj, attr or "register", None)
        if not callable(fn):
            raise PluginError(f"{module} has no register(registry) function")
        version = getattr(obj, "__version__", None)
        return self.register_plugin(name or mod_name.split(".")[0], fn, module=module, version=version)

    def load_entry_points(self, enabled: str | None = None) -> list[PluginInfo]:
        """Load installed plugins whose entry-point name is enabled (ALC_PLUGINS)."""
        enabled = os.environ.get("ALC_PLUGINS", "*") if enabled is None else enabled
        wanted = {p.strip() for p in enabled.split(",") if p.strip()}
        if not wanted or wanted == {"none"}:
            return []
        loaded = []
        found = set()
        for ep in sorted(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
            found.add(ep.name)
            if "*" not in wanted and ep.name not in wanted:
                continue
            target = ep.load()
            fn = getattr(target, "register", target)
            if not callable(fn):
                raise PluginError(f"entry point {ep.name} ({ep.value}) is not callable and has no register()")
            dist = getattr(ep, "dist", None)
            loaded.append(self.register_plugin(
                ep.name, fn, module=ep.value, distribution=dist.name if dist else None,
                version=dist.version if dist else None))
        missing = wanted - found - {"*"}
        if missing:
            raise PluginError(f"ALC_PLUGINS names plugins that are not installed: {sorted(missing)}")
        return loaded

    # ---------- use ----------
    def tools_for(self, phase: str) -> list[Tool]:
        return [t for t in self.tools if phase in t.phases]

    def summary(self) -> list[dict]:
        """Loaded plugins and versions, as recorded in each run manifest."""
        return [p.as_dict() for p in self.plugins.values()]

    def method_docs(self) -> dict[str, str]:
        return {n: (getattr(fn, "__doc__", "") or "").strip().split("\n")[0] for n, fn in self.analysis_methods.items()}


_registry: Registry | None = None


def get_registry() -> Registry:
    """The process-wide registry: built-ins, the installed plugins ALC_PLUGINS enables, and the modules in
    ALC_PLUGIN_MODULES; loaded once per process (the sandbox kernel loads its own from the same environment)."""
    global _registry
    if _registry is None:
        r = Registry()
        r.load_entry_points()
        for module in os.environ.get("ALC_PLUGIN_MODULES", "").split(","):
            if module.strip():
                r.load_module(module.strip())
        _registry = r
    return _registry


def set_registry(registry: Registry | None) -> None:
    """Replace (or with None, reset) the process-wide registry; for tests and embedding."""
    global _registry
    _registry = registry


# ---------------------------------------------------------------- built-ins

def _s3_setup(con) -> None:
    from .paths import duckdb_s3_secret

    con.execute("install httpfs; load httpfs")  # a no-op when the image ships the extension
    con.execute(duckdb_s3_secret())


def _register_builtins(r: Registry) -> None:
    from .paths import parse_yaml_text

    r.data_formats.add(DataFormat("parquet", "read_parquet", description="Apache Parquet"))
    r.data_formats.add(DataFormat("csv", "read_csv_auto", description="CSV with type sniffing"))
    r.data_formats.add(DataFormat("json", "read_json_auto", description="JSON or newline-delimited JSON"))

    r.storage_backends.add(StorageBackend("file", lambda con: None, "local folders"))
    r.storage_backends.add(StorageBackend("s3", _s3_setup, "S3 through httpfs: the run's own credentials, else the AWS credential chain; AWS_ENDPOINT_URL_S3 honoured"))

    r.metadata_parsers.add(MetadataParser("yaml", (".yaml", ".yml", ".json", ""), parse_yaml_text,
                                          "YAML or JSON (YAML is a superset of JSON)"))

    def heuristic(req: DraftRequest) -> dict:
        from .catalog import heuristic_draft
        return heuristic_draft(req.dataset_id, req.metadata, req.sample_columns)

    def model(req: DraftRequest) -> dict:
        from .catalog import llm_draft
        return llm_draft(req.settings, req.dataset_id, req.metadata_text, req.sample, req.sample_columns)

    r.card_drafters.add(CardDrafter("heuristic", heuristic, "maps common metadata spellings, no model"))
    r.card_drafters.add(CardDrafter("model", model, "the sub model (ALC_SUB_MODEL) reads the metadata file",
                                    drafted_by=lambda s: s.sub_model))

    def bedrock(model_id: str, settings):
        from pydantic_ai.models.bedrock import BedrockConverseModel
        from pydantic_ai.providers.bedrock import BedrockProvider
        return BedrockConverseModel(model_id, provider=BedrockProvider(region_name=settings.aws_region))

    r.model_providers.add(ModelProvider("bedrock", bedrock, "Amazon Bedrock Converse in AWS_REGION"))

    def yaml_access(settings):
        from .access import AccessModel
        return AccessModel.load(settings.access_file)

    r.access_providers.add(AccessProvider("yaml", yaml_access, "users, plants and workspaces from ALC_ACCESS"))

    def oidc_access(settings):
        from .access import ClaimsOnlyAccessModel
        return ClaimsOnlyAccessModel.load(settings.access_file)

    for name in ("oidc", "alb-oidc"):
        r.access_providers.add(AccessProvider(name, oidc_access, "identity-provider group claims only (the API passes "
                                              "verified claims, e.g. from the ALB's x-amzn-oidc-data); plants, "
                                              "workspaces and group mapping from ALC_ACCESS; local users refused"))
