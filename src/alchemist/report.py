"""Conclusion card and final report, filled from recorded findings."""

from __future__ import annotations

import re

import markdown
from jinja2 import Environment, select_autoescape

from .paths import write_text

PLACEHOLDER = re.compile(r"\{\{\s*(F\d+)(?:\.(value))?\s*\}\}")
TYPED_NUMBER = re.compile(
    r"(?<![\w.-])\d+(?:[.,]\d+)?\s*(?:%|°C|°F|percentage points|points|pts|bar|ppm|kg|mpm|m/min)"
    r"|(?<![\w.-])\d+\.\d+(?![\w.-])")


class ReportError(ValueError):
    pass


def fmt(value, unit: str = "") -> str:
    if isinstance(value, float):
        text = f"{value:,.2f}" if abs(value) >= 1 else f"{value:.3g}"
    else:
        text = str(value)
    return f"{text} {unit}".strip() if unit else text


def fill(text: str, findings: dict[str, dict]) -> str:
    stripped = PLACEHOLDER.sub("", text)
    typed = TYPED_NUMBER.findall(stripped)
    if typed:
        raise ReportError(f"measured numbers must come from findings as {{{{Fn}}}} placeholders; found typed: {typed[:5]}")

    def repl(m):
        fid, part = m.group(1), m.group(2)
        if fid not in findings:
            raise ReportError(f"{fid} is not a recorded finding")
        f = findings[fid]
        return fmt(f["value"]) if part == "value" else fmt(f["value"], f.get("unit", ""))

    return PLACEHOLDER.sub(repl, text)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{{ title }}</title>
<style>
 body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;color:#1d1d1f;line-height:1.5}
 h1{font-size:1.6rem} h2{font-size:1.2rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.2rem}
 .card{border:2px solid #2f6fdf;border-radius:8px;padding:1rem 1.2rem;background:#f4f8ff}
 table{border-collapse:collapse;width:100%;font-size:.9rem} td,th{border:1px solid #ddd;padding:.35rem .5rem;text-align:left;vertical-align:top}
 code,pre{font-size:.82rem;background:#f5f5f5} pre{padding:.6rem;overflow-x:auto}
 .muted{color:#666;font-size:.85rem} img{max-width:100%;border:1px solid #eee}
 .supported{color:#0a7d32} .refuted{color:#b3261e} .inconclusive{color:#8a6d00}
</style></head><body>
<h1>{{ title }}</h1>
<p class="muted">Run {{ run_id }} · {{ created }} · asked by {{ asker }} · workspace {{ workspace }} · plants {{ plants|join(', ') }}</p>

<h2>Question, scope and baseline</h2>
<p>{{ question }}</p>
<table><tr><th>Metric</th><td>{{ plan.metric }}</td></tr>
<tr><th>Period</th><td>{{ plan.scope.period|join(' to ') }}{% if plan.scope.line %} · line {{ plan.scope.line }}{% endif %}</td></tr>
<tr><th>Baseline</th><td>{{ plan.scope.baseline|join(' to ') }}{% if plan.scope.baseline_reason %} · {{ plan.scope.baseline_reason }}{% endif %}</td></tr></table>

<h2>Conclusion</h2>
<div class="card">{{ conclusion_html|safe }}
{% if top_causes %}<p><b>Top causes</b></p><ol>{% for c in top_causes %}<li>{{ c.cause }} ({{ c.confidence }} confidence; evidence {{ c.finding }})</li>{% endfor %}</ol>{% endif %}
{% if ruled_out %}<p><b>Ruled out:</b> {{ ruled_out|join('; ') }}</p>{% endif %}
{% if next_check %}<p><b>Recommended next check:</b> {{ next_check }}</p>{% endif %}
</div>

<h2>Data used</h2>
<table><tr><th>Query</th><th>Datasets (card version)</th><th>Rows</th><th>Source objects</th><th>Snapshot</th></tr>
{% for qid, q in queries.items() %}<tr><td>{{ qid }}</td><td>{% for d, v in q.card_versions.items() %}{{ d }}@{{ v }} {% endfor %}</td><td>{{ q.rows }}</td><td>{{ q.objects|length }}</td><td><code title="{{ q.snapshot }}">{{ q.snapshot[:19] }}…</code></td></tr>{% endfor %}</table>
{% for qid, q in queries.items() %}<details><summary>{{ qid }} SQL</summary><pre>{{ q.sql }}</pre></details>{% endfor %}

<h2>Method</h2>
<table><tr><th>Hypothesis</th><th>Method</th><th>Source</th></tr>
{% for h in plan.hypotheses %}<tr><td>{{ h.id }}: {{ h.cause }}</td><td>{{ h.method }}</td><td>{{ h.source or '' }}</td></tr>{% endfor %}</table>
{% if method_notes %}<p>{{ method_notes }}</p>{% endif %}

<h2>Findings</h2>
<table><tr><th>Finding</th><th>Hypothesis</th><th>Statement</th><th>Value</th><th>Verdict</th><th>Confidence</th><th>Evidence</th></tr>
{% for f in findings %}<tr><td>{{ f.id }}</td><td>{{ f.hypothesis }}</td><td>{{ f.statement }}</td><td>{{ fmt(f.value, f.unit) }}</td>
<td class="{{ f.verdict }}">{{ f.verdict }}</td><td>{{ f.confidence }}</td><td>{{ f.evidence|join(', ') }}</td></tr>{% endfor %}</table>
{% for fig in figures %}<p><img src="../{{ fig }}" alt="{{ fig }}"><br><span class="muted">{{ fig }}</span></p>{% endfor %}

<h2>Independent review</h2>
<p>{{ review.notes or '' }}</p>
{% if review.issues %}<ul>{% for i in review.issues %}<li>{{ i.finding }}: {{ i.problem }}{% if i.suggestion %} ({{ i.suggestion }}){% endif %}</li>{% endfor %}</ul>{% else %}<p>No open issues.</p>{% endif %}

<h2>Domain knowledge cited</h2>
{% if citations %}<ul>{% for c in citations %}<li>{{ c.doc }} v{{ c.version }}, {{ c.section }}{% if c.stale %} <b>(past review date)</b>{% endif %}</li>{% endfor %}</ul>{% else %}<p>None.</p>{% endif %}

<h2>Limitations and open questions</h2>
{% if limitations %}<ul>{% for l in limitations %}<li>{{ l }}</li>{% endfor %}</ul>{% else %}<p>None stated.</p>{% endif %}

<h2>Reproduction</h2>
<p>Run folder: <code>{{ run_path }}</code></p>
<p>Replay exactly from the stored snapshots: <code>alchemist replay {{ run_id }}</code>. Re-run the same code on current data: <code>alchemist replay {{ run_id }} --mode rerun</code>. Or open <code>notebook.ipynb</code> with <code>ALC_RUN_DIR</code> set to the run folder, set <code>run_mode = "replay"</code> and run all cells.</p>
<p class="muted">Environment: Python {{ env.python }} · duckdb {{ env.packages.duckdb }} · pandas {{ env.packages.pandas }} · lead model {{ models.lead }} · prompts {{ models.prompts }}</p>
</body></html>"""


def render(run_dir, *, ctx: dict, report_input: dict, findings: list[dict], queries: dict) -> dict:
    by_id = {f["id"]: f for f in findings}
    conclusion = fill(report_input["conclusion"], by_id)
    top = report_input.get("top_causes", [])
    for c in top:
        c["cause"] = fill(c["cause"], by_id)
    figures = sorted({e for f in findings for e in f.get("evidence", []) if str(e).startswith("figures/")})
    env = Environment(autoescape=select_autoescape(["html"]))
    env.globals["fmt"] = fmt
    html = env.from_string(TEMPLATE).render(
        title=report_input["title"], conclusion_html=markdown.markdown(conclusion), top_causes=top,
        ruled_out=report_input.get("ruled_out", []), next_check=report_input.get("next_check", ""),
        method_notes=report_input.get("method_notes", ""), limitations=report_input.get("limitations", []),
        findings=findings, queries=queries, figures=figures, **ctx)

    card = [f"# {report_input['title']}", "", conclusion.strip(), ""]
    if top:
        card += ["**Top causes**", ""] + [f"{i}. {c['cause']} ({c['confidence']} confidence; {c['finding']})" for i, c in enumerate(top, 1)] + [""]
    if report_input.get("ruled_out"):
        card += [f"**Ruled out:** {'; '.join(report_input['ruled_out'])}", ""]
    if report_input.get("next_check"):
        card += [f"**Next check:** {report_input['next_check']}", ""]
    card += [f"Run `{ctx['run_id']}` · report: report/report.html · notebook: notebook.ipynb", ""]
    write_text(run_dir / "report" / "conclusion.md", "\n".join(card))
    write_text(run_dir / "report" / "report.html", html)
    return {"conclusion": "report/conclusion.md", "report": "report/report.html", "notebook": "notebook.ipynb",
            "conclusion_text": "\n".join(card)}
