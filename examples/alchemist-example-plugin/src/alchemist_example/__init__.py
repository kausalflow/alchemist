"""Example Alchemist plugin.

Shows one contribution of most kinds. Install it next to Alchemist
(`uv pip install -e examples/alchemist-example-plugin`) and it loads through
its `alchemist.plugins` entry point; or, without installing, put `src/` on
PYTHONPATH and set ALC_PLUGIN_MODULES=alchemist_example.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from alchemist.plugins import (CliCommand, DataFormat, KnowledgePack, MetadataParser, Registry, ReportRenderer,
                               Tool)

__version__ = "0.1.0"
HERE = Path(__file__).parent


# ---------------------------------------------------------------- analysis method (runs in the sandbox kernel)

def cusum(values, target: float | None = None, k: float = 0.5, h: float = 5.0) -> dict:
    """Two-sided CUSUM: first index where the mean shifts by about k standard deviations (alarm at h)."""
    import numpy as np

    x = np.asarray(values, dtype=float)
    x = x[~np.isnan(x)]
    mu = float(np.mean(x)) if target is None else float(target)
    sd = float(np.std(x, ddof=1)) or 1.0
    z = (x - mu) / sd
    hi = lo = 0.0
    for i, v in enumerate(z):
        hi, lo = max(0.0, hi + v - k), min(0.0, lo + v + k)
        if hi > h or lo < -h:
            return {"alarm_index": i, "direction": "up" if hi > h else "down", "target": mu, "sd": sd, "n": len(x)}
    return {"alarm_index": None, "direction": None, "target": mu, "sd": sd, "n": len(x)}


# ---------------------------------------------------------------- agent tool (runs in the orchestrator)

def _glossary() -> dict:
    import yaml

    return yaml.safe_load((HERE / "knowledge" / "structured" / "glossary.yaml").read_text())


def define_term(ctx, inp: dict) -> str:
    terms = _glossary()["terms"]
    key = next((t for t in terms if t.lower() == inp["term"].strip().lower()), None)
    if key is None:
        return f"No glossary entry for {inp['term']!r}. Known terms: {', '.join(sorted(terms))}"
    ctx.cite("GLOSS-EXT", "1", "Extrusion glossary", key)
    return f"{key}: {terms[key]}"


# ---------------------------------------------------------------- report renderer

def markdown_report(run_dir, report: dict) -> list[str]:
    lines = [report["conclusion_text"], "", "## Findings", "", "| Finding | Statement | Value | Verdict |",
             "| --- | --- | --- | --- |"]
    for f in report["findings"]:
        lines.append(f"| {f['id']} | {f['statement']} | {f['value']} {f.get('unit', '')} | {f['verdict']} |")
    target = run_dir / "report" / "report.md"
    target.write_text("\n".join(lines) + "\n")
    return ["report/report.md"]


# ---------------------------------------------------------------- CLI command

def _cli_args(parser) -> None:
    parser.add_argument("term", nargs="?")


def _cli_run(args) -> None:
    terms = _glossary()["terms"]
    for k in sorted(terms) if not args.term else [args.term.upper()]:
        print(f"{k}: {terms.get(k, '(unknown)')}")


# ---------------------------------------------------------------- registration

def register(r: Registry) -> None:
    r.analysis_methods.add("cusum", cusum)
    r.tools.add(Tool(
        name="define_term",
        description="Look up a plant or process term (FPY, MFI, CUSUM) in the extrusion glossary. Cites the glossary.",
        input_schema={"type": "object", "properties": {"term": {"type": "string"}}, "required": ["term"]},
        handler=define_term,
        phases=("plan", "analysis"),
    ))
    r.knowledge_packs.add(KnowledgePack("extrusion-basics", HERE / "knowledge", version=__version__,
                                        description="extrusion glossary and term definitions"))
    r.data_formats.add(DataFormat("tsv", "read_csv", options="delim = '\\t', header = true",
                                  description="tab-separated values"))
    r.metadata_parsers.add(MetadataParser("toml", (".toml",), tomllib.loads, "TOML metadata files"))
    r.report_renderers.add(ReportRenderer("markdown", markdown_report, "report/report.md next to the HTML report"))
    r.cli_commands.add(CliCommand("glossary", "print extrusion glossary terms", _cli_run, _cli_args))
