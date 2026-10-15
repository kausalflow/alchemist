# alchemist-example-plugin

A small, working Alchemist plugin to copy from. It contributes:

| Kind | Name | What it does |
| --- | --- | --- |
| analysis method | `cusum` | `alc.methods.cusum(values)` in the kernel: two-sided CUSUM shift detection |
| tool | `define_term` | the agent looks up FPY, MFI, CUSUM in a glossary and cites it |
| knowledge pack | `extrusion-basics` | a glossary document and `glossary` structured knowledge |
| data format | `tsv` | datasets registered with `--format tsv` |
| metadata parser | `toml` | dataset metadata files ending in `.toml` |
| report renderer | `markdown` | writes `report/report.md` into every run |
| CLI command | `glossary` | `alchemist glossary MFI` |

```bash
uv pip install -e examples/alchemist-example-plugin
uv run alchemist plugins show
```

See the [plugins guide](../../docs/guides/plugins.md).
