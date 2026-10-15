# Extending Alchemist with plugins

Alchemist is built to be extended without forking it. A **plugin** is an ordinary Python package that registers
contributions (a data format, an agent tool, an analysis method, a knowledge pack, ...) with Alchemist's plugin
registry. Alchemist's own defaults are registered through the same registry, as the plugin `builtin`.

A working example to copy lives in
[`examples/alchemist-example-plugin`](https://github.com/kausalflow/alchemist/tree/main/examples/alchemist-example-plugin).

## Extension points

| Registry | Contribution | Used for | Built-ins |
| --- | --- | --- | --- |
| `data_formats` | `DataFormat(name, reader, options)` | file formats a dataset can be registered with; `reader` is the DuckDB table function | `parquet`, `csv`, `json` |
| `storage_backends` | `StorageBackend(scheme, setup)` | URI schemes the sandbox's DuckDB can read; `setup(con)` loads extensions and creates secrets | `file`, `s3` |
| `metadata_parsers` | `MetadataParser(name, suffixes, parse)` | reading a dataset's own metadata file, chosen by suffix | `yaml` (YAML and JSON) |
| `card_drafters` | `CardDrafter(name, draft)` | turning metadata plus a data sample into a catalog card | `heuristic`, `model` |
| `model_providers` | `ModelProvider(name, build)` | a `provider:` prefix in model specs, built as a Pydantic AI model | `bedrock` |
| `access_providers` | `AccessProvider(name, load)` | where users, plant groups and workspaces come from | `yaml`, `oidc` (alias `alb-oidc`) |
| `tools` | `Tool(name, description, input_schema, handler, phases)` | extra agent tools in the planning and/or analysis step | |
| `analysis_methods` | any function | `alc.methods.<name>(...)` in the sandbox kernel | |
| `knowledge_packs` | `KnowledgePack(name, root, version)` | folders of documents and structured knowledge, searched with `ALC_KNOWLEDGE` | |
| `report_renderers` | `ReportRenderer(name, render)` | extra report files written into the run folder before sealing | |
| `cli_commands` | `CliCommand(name, help, run, add_arguments)` | extra `alchemist <command>` subcommands | |

All contribution types are importable from `alchemist.plugins`.

## Write a plugin

A plugin is a function that receives the registry:

```python
# acme_alchemist/__init__.py
from alchemist.plugins import DataFormat, Registry, Tool

__version__ = "1.0.0"


def cusum(values, target=None, k=0.5, h=5.0):
    """Two-sided CUSUM shift detection."""
    ...


def lookup_batch(ctx, inp):
    # ctx.plants, ctx.user_id, ctx.run, ctx.cite(doc, version, title, section)
    return f"batch {inp['batch_id']}: ..."


def register(r: Registry) -> None:
    r.analysis_methods.add("cusum", cusum)
    r.data_formats.add(DataFormat("delta", "delta_scan"))
    r.tools.add(Tool(
        name="lookup_batch",
        description="Look up a batch record in the MES.",
        input_schema={"type": "object", "properties": {"batch_id": {"type": "string"}}, "required": ["batch_id"]},
        handler=lookup_batch,
        phases=("analysis",),
    ))
```

and declares it as an entry point in its `pyproject.toml`:

```toml
[project.entry-points."alchemist.plugins"]
acme = "acme_alchemist:register"
```

Install the package next to Alchemist (in the same image) and check what loaded:

```bash
uv pip install -e path/to/acme-alchemist
alchemist plugins list    # plugins, versions and what each contributed
alchemist plugins show    # every extension point and which plugin provides each name
```

### Modules without packaging

For a site's own code, or while developing, a plugin module does not need to be packaged. Put it on `PYTHONPATH` and
name it in `ALC_PLUGIN_MODULES`:

```bash
export PYTHONPATH=/opt/site-plugins
export ALC_PLUGIN_MODULES=acme_site.quality,acme_site.kiln:register
```

### Replacing a default

Names are unique per extension point. Registering a name that already exists is an error, so two plugins cannot
silently fight over it. To swap a default deliberately, pass `replace=True`:

```python
r.data_formats.add(DataFormat("csv", "read_csv", options="delim = ';', decimal_separator = ','"), replace=True)
```

`alchemist plugins list` marks replaced names.

## Where plugin code runs

The [target design](../design.md) separates the orchestrator, which talks to the model, from the sandbox, which reads
the lake. Plugins respect the same split:

| Runs in the orchestrator (no lake rows) | Runs in the sandbox kernel (run's data credentials, no model) |
| --- | --- |
| tools, model providers, access providers, card drafters, metadata parsers, report renderers, CLI commands | analysis methods, storage backends, data formats (when views are built) |

A tool that needs data should not read the lake itself: give the agent an analysis method instead, which it calls in
`run_python`, so the code and its output land in the run's notebook and replay with it.

## Choosing and enabling plugins

| Setting | Default | Meaning |
| --- | --- | --- |
| `ALC_PLUGINS` | `*` | installed plugins (entry-point names) to load: comma-separated, `*` for all, `none` for built-ins only. Naming one that is not installed is an error |
| `ALC_PLUGIN_MODULES` | unset | importable modules to load as plugins without an entry point |
| `ALC_ACCESS_PROVIDER` | `yaml` | which access provider supplies users and groups (`oidc` in production) |
| `ALC_ALLOWED_PROVIDERS` | `bedrock` | model providers plant data may go to; this applies to plugin providers too |

Card drafters are chosen per call (`alchemist catalog index --drafter NAME`); data formats per dataset
(`alchemist catalog register --format NAME`). Tools, analysis methods, knowledge packs and report renderers from every
loaded plugin are active.

## Reproducibility

- Each run's `manifest.json` lists the loaded plugins with their versions and contributions (`plugins`).
- Plugin tool definitions are part of the prompt hash (`model.prompts` in the manifest).
- A replay reports `plugin_drift` (plugins added, removed or at another version since the original run).
- Structured knowledge from packs is copied into the run's inputs like any other; citations record the document id
  and version.

## Security

Installing a plugin means running its code with Alchemist's permissions, so treat plugins like any other dependency:
pin them in the image, review them, and keep `ALC_PLUGINS` explicit in production. A plugin model provider gets plant
data only after an operator lists it in `ALC_ALLOWED_PROVIDERS`. Tools only see what a `RunContext` exposes (the run's
plants, user and folder), and every tool call is logged in the run transcript.

## Test a plugin

```python
from alchemist.plugins import Registry, set_registry


def test_my_plugin():
    r = Registry()
    r.load_module("acme_alchemist")
    set_registry(r)          # make it the process-wide registry for code under test
    try:
        assert "cusum" in r.analysis_methods
    finally:
        set_registry(None)
```

`tests/test_plugins.py` in this repository runs a full scripted investigation with the example plugin loaded.
