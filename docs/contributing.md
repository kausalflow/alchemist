# Contributing

## Development setup

```bash
uv sync --extra dev
uv run pytest -q
```

CI runs the test suite and the offline demo end to end (`alchemist demo setup && alchemist demo run`) on every pull
request. A change that touches the run loop should keep the demo's replay at zero mismatches.

## Working on the documentation

The site is built with [Zensical](https://zensical.org). Its configuration is `zensical.toml` at the repository root
and the pages are in `docs/`.

```bash
uvx zensical serve    # live preview on http://localhost:8000
uvx zensical build    # static site in ./site
```

When you add a page, add it to `nav` in `zensical.toml`. Diagrams are written as fenced `mermaid` blocks.

Keep pages close to the code: when you change a command, an environment variable, a run file or a tool, update the
matching page under `docs/reference/`.

## Conventions

- All file access goes through `alchemist.paths` (cloudpathlib), so code works on S3 and locally.
- Every YAML and JSON file Alchemist writes carries a `schema` field such as `alchemist.manifest/v1`.
- Numbers that end up in a report come from recorded findings, never from text the model wrote.
- Prompt and tool-definition changes change the prompt hash recorded in manifests; mention them in the pull request.
