"""Demo mode: the offline script, paced for an audience and answering what people do during the demo.

`alchemist demo serve` (or `alchemist serve --scripted`) plans and analyses with `DemoModel` instead of a real
model. It plays the scripted investigation of the demo question (demo/script.py) through the real agent loop and
tools, so queries, notebook cells, figures, findings, review, report, seal and replay are all real, and:

- waits `ALC_DEMO_PACE` seconds before each turn, so the live notebook fills at a pace people can follow
  (0 by default; `alchemist demo serve` uses 2);
- answers notes, comments on notebook cells, "ask for more" at review and a plan sent back: it acknowledges the
  note in the activity feed, adds a matching check to the notebook (picked by keywords in the note), and names the
  note in the report; a plan sent back gets a new hypothesis for what was asked;
- plays a second, short investigation for a follow-up question on a finished answer (FOLLOW_UP_QUESTION);
- looks at a data file attached to the question (an upload) in one extra notebook cell.

The model keeps no state of its own: everything it decides comes from the conversation it is given (which script
turns and which notes it has answered are in the ids of its own tool calls), so it continues correctly after a
pause, a checkpoint restore, a send-back or a restart.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import time

from . import script

FOLLOW_UP_QUESTION = "Which lots ran on R-118, and how much first-pass output did we lose?"

# ---------------------------------------------------------------- follow-up script

FOLLOW_UP_PLAN = {
    "metric": "Line 2 lots that ran on resin lot R-118, and the first-pass output lost on them, in lot-equivalents",
    "scope": {"line": "L2", "plants": ["P01"], "period": ["2026-07-13", "2026-08-09"]},
    "hypotheses": [
        {"id": "H1", "cause": "R-118 was used only from 3 August (no earlier lots to recall)",
         "method": "List every Line 2 lot by resin lot and the first and last start time of R-118 lots",
         "datasets": ["quality_lots"], "source": "earlier answer F2"},
        {"id": "H2", "cause": "The output lost on R-118 lots is material (more than one lot of first-pass output)",
         "method": "Sum, over R-118 lots, the gap to the mean yield of other lots in the same week; express in lot-equivalents",
         "datasets": ["quality_lots"], "source": "earlier answer F2"},
    ],
    "notes": "Builds on the earlier answer: re-queries the lots so the numbers stand on their own.",
}

FOLLOW_UP_PLAN_TURNS = [
    [("text", "This builds on the earlier answer, which found R-118 lots yielded less. To **list the affected lots** and "
              "**size the loss** I need every Line 2 lot with its resin lot and yield."),
     ("search_catalog", {"query": "line 2 lots resin lot first pass yield"})],
    [("submit_plan", FOLLOW_UP_PLAN)],
]

AFFECTED = """import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

lots = df_q1.copy()
r118 = lots[lots.resin_lot == "R-118"].sort_values("start_ts")
same_week_other = lots[(lots.resin_lot != "R-118") & (lots.start_ts >= pd.Timestamp("2026-08-03"))]
expected = same_week_other.first_pass_yield_pct.mean()
r118 = r118.assign(lost_points=(expected - r118.first_pass_yield_pct).clip(lower=0))
lost_lots = r118.lost_points.sum() / 100
first_r118, last_r118 = r118.start_ts.min(), r118.start_ts.max()
print(f"R-118 lots: {len(r118)}, from {first_r118:%d %b %H:%M} to {last_r118:%d %b %H:%M}")
print(f"expected yield (other resin, same week): {expected:.2f} %; R-118 mean: {r118.first_pass_yield_pct.mean():.2f} %")
print(f"first-pass output lost: {lost_lots:.2f} lot-equivalents")
print(r118[["lot_id", "start_ts", "crew", "first_pass_yield_pct", "lost_points"]].round(2).to_string(index=False))

daily = r118.groupby(r118.start_ts.dt.date).agg(lots=("lot_id", "count"), lost=("lost_points", "sum"))
fig, ax = plt.subplots(figsize=(7, 3.4))
ax.bar(pd.to_datetime(daily.index), daily["lost"] / 100, color="#E45756")
for d, row in daily.iterrows():
    ax.annotate(f"{int(row.lots)} lots", (pd.Timestamp(d), row.lost / 100), ha="center", fontsize=8,
                xytext=(0, 3), textcoords="offset points")
ax.set_ylabel("First-pass output lost, lot-equivalents")
ax.set_title(f"R-118 lots by day: {lost_lots:.1f} lot-equivalents lost in total")
plt.tight_layout()
plt.show()"""

FOLLOW_UP_ANALYSIS_TURNS = [
    [("text", "Re-querying **every Line 2 lot** with its resin lot, so this answer stands on its own."),
     ("run_query", {"sql": "select lot_id, start_ts, resin_lot, crew, first_pass_yield_pct from ds.quality_lots "
                           "where line = 'L2' order by start_ts",
                    "purpose": "Every Line 2 lot with resin lot, crew and yield"})],
    [("text", "Listing the **R-118 lots** and sizing the loss against lots on other resin in the same week."),
     ("run_python", {"code": AFFECTED, "purpose": "The R-118 lots, when they ran, and the first-pass output lost on them (H1, H2)"})],
    [("record_finding", {"hypothesis": "H1", "statement": "Line 2 lots that ran on R-118, all of them from 3 August onwards.",
                         "value_expression": "len(r118)", "unit": "lots", "verdict": "supported",
                         "confidence": "high", "evidence": ["q1", "figure"]}),
     ("record_finding", {"hypothesis": "H2", "statement": "First-pass output lost on R-118 lots against other resin in the same week.",
                         "value_expression": "round(lost_lots, 2)", "unit": "lot-equivalents", "verdict": "supported",
                         "confidence": "medium", "evidence": ["q1", "figure"]})],
    [("finish_analysis", {"summary": "All R-118 lots ran from 3 August; the first-pass output lost on them adds up to more than one lot."})],
    [("build_report", {
        "title": "Line 2 lots on R-118 and the output lost",
        "conclusion": "On Line 2, {{F1}} ran on resin lot R-118, all of them from 3 August, so no earlier lots are "
                      "affected. Against lots on other resin in the same week, they lost {{F2}} of first-pass output. "
                      "The lot list is in the notebook for quality to review and hold.",
        "top_causes": [{"cause": "Resin lot R-118 (from the earlier answer)", "confidence": "high", "finding": "F1"}],
        "ruled_out": ["R-118 lots before 3 August (F1)"],
        "next_check": "Hold the remaining R-118 bags and check whether the listed lots need extra inspection before release.",
        "method_notes": "The expected yield of each R-118 lot is the mean yield of lots on other resin in the same week; "
                        "the loss is the sum of the shortfalls, divided by 100 to give lot-equivalents.",
        "limitations": ["Lots differ in length; lot-equivalents assume equal output per lot."],
    })],
]

FOLLOW_UP_REVIEW = {
    "ok": True,
    "issues": [{"finding": "F2", "problem": "The loss assumes equal output per lot.",
                "suggestion": "State the assumption; use metres per lot if the MES has it."}],
    "notes": "Both values are computed in the notebook cell cited, and the lot list matches the R-118 lots of the earlier answer.",
}

# ---------------------------------------------------------------- checks added when someone writes a note

PREAMBLE = """import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
lots = df_q1.copy()
"""

CHECKS = [
    (r"crew|shift|operator|team|night", "Yield by crew and resin lot in the scope week", PREAMBLE + """in_scope = lots[lots.period == "scope"].copy()
in_scope["resin"] = np.where(in_scope.resin_lot == "R-118", "R-118", "other")
table = in_scope.pivot_table(index="crew", columns="resin", values="first_pass_yield_pct", aggfunc="mean").round(2)
print(table)
ax = table.plot.bar(figsize=(6, 3.4), rot=0, color=["#E45756", "#4C78A8"])
ax.set_ylabel("Mean first-pass yield, %")
ax.set_title("R-118 lots yield less on every crew")
plt.tight_layout()
plt.show()"""),
    (r"temperat|setpoint|zone|melt|barrel|tt-204|heat", "Melt temperature, scope week against baseline", PREAMBLE + """proc = df_q2.dropna(subset=["barrel_temp_c"]).copy()
proc["period"] = np.where(proc.ts >= pd.Timestamp("2026-08-03"), "scope", "baseline")
print(proc.groupby("period").barrel_temp_c.describe().round(2))
fig, ax = plt.subplots(figsize=(7, 3.4))
for name, g in proc.groupby("period"):
    ax.hist(g.barrel_temp_c, bins=60, alpha=0.6, density=True, label=name)
ax.axvspan(228, 234, color="grey", alpha=0.12, lw=0, label="SOP-114 window")
ax.axvline(229, color="grey", ls="--", lw=1)
ax.set_xlabel("Barrel zone 3 temperature, degC")
ax.set_title("The melt ran cooler in the scope week")
ax.legend(fontsize=8)
plt.tight_layout()
plt.show()"""),
    (r"supplier|mfi|melt flow|certif|resin|material", "Resin certificate MFI against lot yield", PREAMBLE + """by_resin = lots.groupby("resin_lot").first_pass_yield_pct.agg(["mean", "count"]).reset_index()
by_resin = by_resin.merge(df_q3, on="resin_lot", how="left")
print(by_resin.round(2))
fig, ax = plt.subplots(figsize=(6, 3.4))
ax.scatter(by_resin.cert_mfi_g10min, by_resin["mean"], s=30 + 3 * by_resin["count"], color="#4C78A8")
for _, r in by_resin.iterrows():
    ax.annotate(f"{r.resin_lot} ({r.supplier})", (r.cert_mfi_g10min, r["mean"]), fontsize=8, xytext=(4, 4), textcoords="offset points")
ax.set_xlabel("Certificate melt flow index, g/10 min")
ax.set_ylabel("Mean first-pass yield, %")
ax.set_title("The low-MFI resin lot has the lowest yield")
plt.tight_layout()
plt.show()"""),
    (r"speed|haul|throughput|rate", "Line speed by day", PREAMBLE + """proc = df_q2.set_index("ts")
daily = proc.line_speed_mpm.resample("D").agg(["mean", "std"]).round(2)
print(daily.tail(10))
fig, ax = plt.subplots(figsize=(7, 3.2))
ax.plot(daily.index, daily["mean"], marker="o", ms=3)
ax.axvline(pd.Timestamp("2026-08-03"), color="grey", ls="--", lw=1)
ax.set_ylabel("Line speed, m/min")
ax.set_title("Line speed stayed flat through the scope week")
plt.tight_layout()
plt.show()"""),
    (r"pressure|die|pt-210", "Melt pressure, scope week against baseline", PREAMBLE + """proc = df_q2.copy()
proc["period"] = np.where(proc.ts >= pd.Timestamp("2026-08-03"), "scope", "baseline")
print(proc.groupby("period").melt_pressure_bar.describe().round(1))
hourly = proc.set_index("ts").melt_pressure_bar.resample("h").mean()
fig, ax = plt.subplots(figsize=(7, 3.2))
ax.plot(hourly.index, hourly.values, lw=0.8)
ax.axvline(pd.Timestamp("2026-08-03"), color="grey", ls="--", lw=1)
ax.set_ylabel("Melt pressure, bar")
ax.set_title("Melt pressure rose with the R-118 lots")
plt.tight_layout()
plt.show()"""),
]

DEFAULT_CHECK = ("Daily yield and lot count", PREAMBLE + """lots["day"] = lots.start_ts.dt.date
daily = lots.groupby("day").agg(lots=("lot_id", "count"), mean_fpy=("first_pass_yield_pct", "mean"),
                                r118_share=("resin_lot", lambda s: (s == "R-118").mean())).round(2)
print(daily.tail(12))
fig, ax = plt.subplots(figsize=(7, 3.2))
ax.plot(pd.to_datetime(daily.index), daily.mean_fpy, marker="o", ms=3, label="mean first-pass yield")
ax2 = ax.twinx()
ax2.bar(pd.to_datetime(daily.index), daily.r118_share, alpha=0.2, color="#E45756", label="share of R-118 lots")
ax.set_ylabel("First-pass yield, %")
ax2.set_ylabel("Share of R-118 lots")
ax.set_title("Daily yield falls as R-118 lots take over")
plt.tight_layout()
plt.show()""")

UPLOAD = """up = df_{qid}.copy()
print(up.shape[0], "rows,", up.shape[1], "columns")
print(up.describe(include="all").T.head(12))
num = [c for c in up.select_dtypes("number").columns]
if "resin_lot" in up.columns and num:
    print(up.groupby("resin_lot")[num].mean().round(2))
key = next((c for c in ("lot_id", "lot", "lot_no", "lot_number") if c in up.columns), None)
num = [c for c in num if c not in ("first_pass_yield_pct",)]
if key and "first_pass_yield_pct" not in up.columns:
    joined = up.merge(df_q1[["lot_id", "resin_lot", "first_pass_yield_pct"]].rename(columns={"lot_id": key}),
                      on=key, how="inner", suffixes=("", "_lots"))
    print("rows that match a Line 2 lot:", len(joined))
    if num and len(joined):
        fig, ax = plt.subplots(figsize=(6, 3.4))
        ax.scatter(joined[num[0]], joined.first_pass_yield_pct, s=14,
                   c=(joined.get("resin_lot_lots", joined.get("resin_lot")) == "R-118"), cmap="coolwarm")
        ax.set_xlabel(num[0])
        ax.set_ylabel("First-pass yield, %")
        ax.set_title(f"Uploaded {num[0]} against lot yield (red: R-118)")
        plt.tight_layout()
        plt.show()"""

# ---------------------------------------------------------------- reading the conversation

_STEER = re.compile(r"(Take it into account from your next step\.|read your report and asked for more|"
                    r"which reached you after you had built the report|sent the plan back for revision)")
_ID = re.compile(r"^toolu_(d|n|u|h)(\d+)_\d+$")
_FIGURES = re.compile(r"Figures: (\[[^\]]*\])")


def _blocks(msg: dict) -> list[dict]:
    c = msg.get("content")
    return [{"type": "text", "text": c}] if isinstance(c, str) else list(c or [])


def _note_text(block: str) -> str:
    """What the person wrote, without the framing the run adds around it."""
    body = []
    for ln in block.splitlines()[1:]:
        if ln.startswith(("Take it into account", "Do what was asked", "Revise the plan", "They wrote it about")):
            break
        body.append(ln.strip())
    text = " ".join(x for x in body if x).strip() or block.strip()
    return re.sub(r"^About (?:cells? )?c\d+(?:(?:, | and )c\d+)*[:.]?\s*", "", text) or text


def _author(block: str) -> str:
    first = block.splitlines()[0] if block else ""
    m = re.match(r"^(?:Note from )?(\S+)", first)
    return m.group(1) if m else "the user"


class Conversation:
    """What the model has done so far and what is waiting for it, read from the messages."""

    def __init__(self, messages: list[dict]):
        self.first = next((b.get("text", "") for b in _blocks(messages[0]) if b.get("type") == "text"), "") if messages else ""
        self.played: set[int] = set()
        self.answered: set[int] = set()
        self.notes: list[dict] = []  # each steering text the run appended, in order
        self.tool_names: list[tuple[str, str]] = []  # (kind, tool) of every call, in order
        self.uploads_seen = False
        self.raised_done: set[int] = set()
        self.script_figures: list[str] = []  # figures of the scripted python cells, in order
        script_ids: set[str] = set()
        for m in messages:
            for b in _blocks(m):
                if m["role"] == "assistant" and b.get("type") == "tool_use":
                    k = _ID.match(b["id"])
                    kind, n = (k.group(1), int(k.group(2))) if k else ("?", -1)
                    if kind == "d":
                        self.played.add(n)
                        script_ids.add(b["id"])
                    elif kind == "n":
                        self.answered.add(n)
                    elif kind == "u":
                        self.uploads_seen = True
                    elif kind == "h":
                        self.raised_done.add(n)
                    self.tool_names.append((kind, b["name"]))
                elif m["role"] == "user" and b.get("type") == "text" and _STEER.search(b.get("text", "")):
                    self.notes.append({"n": len(self.notes), "text": b["text"]})
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in script_ids:
                    for c in b.get("content") or []:
                        f = _FIGURES.search(c.get("text", "")) if isinstance(c, dict) else None
                        if f:
                            try:
                                self.script_figures += ast.literal_eval(f.group(1))
                            except (ValueError, SyntaxError):
                                pass

    @property
    def follow_up(self) -> bool:
        return "This is a follow-up to an earlier answer" in self.first

    @property
    def upload_tables(self) -> list[str]:
        m = re.search(r"Datasets you may query: \[([^\]]*)\]", self.first)
        names = re.findall(r"'(up_[A-Za-z0-9_]+)'", m.group(1)) if m else re.findall(r"ds\.(up_[A-Za-z0-9_]+)", self.first)
        return sorted(set(names))

    @property
    def raised(self) -> list[dict]:
        """Hypotheses people added to the approved plan (a plan sent back), each checked in the notebook."""
        m = re.search(r"Approved plan:\n(.*?)\n\nDatasets you may query", self.first, re.S)
        try:
            plan = json.loads(m.group(1)) if m else {}
        except json.JSONDecodeError:
            return []
        return [h for h in plan.get("hypotheses", []) if str(h.get("cause", "")).startswith("Raised by")]

    def pending(self) -> list[dict]:
        return [n for n in self.notes if n["n"] not in self.answered]

    def report_needs_redo(self) -> str | None:
        """After the script ended: a note answered after the last report means finish and report again."""
        last_report = max((i for i, (_, t) in enumerate(self.tool_names) if t == "build_report"), default=None)
        if last_report is None:
            return None
        after = self.tool_names[last_report + 1:]
        if not any(k == "n" for k, _ in after):
            return None
        tools = [t for _, t in after]
        if "finish_analysis" not in tools:
            return "finish_analysis"
        if "build_report" not in tools[tools.index("finish_analysis"):]:
            return "build_report"
        return None


# ---------------------------------------------------------------- the model

def pace() -> float:
    try:
        return max(0.0, float(os.environ.get("ALC_DEMO_PACE", "0") or 0))
    except ValueError:
        return 0.0


class DemoModel:
    """The demo's planning or analysis model (see the module docstring)."""

    def __init__(self, phase: str, seconds: float | None = None):
        self.phase = "plan" if phase == "plan" else "analysis"
        self.seconds = pace() if seconds is None else seconds
        self.name = "scripted"

    def advance(self, model_calls: int) -> None:
        pass  # the position is read from the conversation

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> dict:
        if self.seconds:
            time.sleep(self.seconds)
        conv = Conversation(messages)
        turn, tag = self._next(conv)
        if turn is None:
            return {"content": [{"type": "text", "text": "Done."}], "stop_reason": "end_turn",
                    "usage": {"input_tokens": 0, "output_tokens": 0}}
        content = [{"type": "text", "text": inp} if name == "text" else
                   {"type": "tool_use", "id": f"toolu_{tag}_{k}", "name": name, "input": inp}
                   for k, (name, inp) in enumerate(turn)]
        calls = any(c["type"] == "tool_use" for c in content)
        return {"content": content, "stop_reason": "tool_use" if calls else "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0}}

    # which script, and what comes next
    def _script(self, conv: Conversation) -> list:
        if self.phase == "plan":
            return FOLLOW_UP_PLAN_TURNS if conv.follow_up else script.PLAN_TURNS
        return FOLLOW_UP_ANALYSIS_TURNS if conv.follow_up else script.ANALYSIS_TURNS

    def _next(self, conv: Conversation):
        turns = self._script(conv)
        pending = conv.pending()
        if pending and (self.phase == "plan" or 0 in conv.played):  # data is loaded once the first turn ran
            note = pending[0]
            return self._react(conv, note), f"n{note['n']}"
        if self.phase == "analysis" and 0 in conv.played and conv.upload_tables and not conv.uploads_seen \
                and not conv.follow_up:
            return self._upload_turn(conv), "u0"
        if self.phase == "analysis" and 0 in conv.played:
            for k, h in enumerate(conv.raised):
                if k not in conv.raised_done:
                    title, code = _check_for(h["cause"])
                    return [("text", f"The approved plan has a hypothesis people added: **{h['id']}**, "
                                     f"{h['cause'].split(': ', 1)[-1]} Checking it now: **{title.lower()}**."),
                            ("run_python", {"code": code, "purpose": f"{title} ({h['id']}, added to the plan)"})], f"h{k}"
        for i, turn in enumerate(turns):
            if i not in conv.played:
                return self._adapt(conv, turn), f"d{i}"
        redo = conv.report_needs_redo() if self.phase == "analysis" else None
        if redo:
            name = redo
            inp = next(inp for t in turns for n, inp in t if n == name)
            return self._adapt(conv, [(name, inp)]), f"d{900 + len(conv.tool_names)}"
        return None, ""

    # adjusting scripted turns to what happened in this run
    def _adapt(self, conv: Conversation, turn: list) -> list:
        out = []
        answered = [n for n in conv.notes if n["n"] in conv.answered]
        for name, inp in turn:
            inp = copy.deepcopy(inp)
            if name == "submit_plan":
                inp = self._plan(conv, inp)
            elif name == "record_finding":
                inp["evidence"] = [self._figure(conv, e) for e in inp.get("evidence", [])]
            elif name == "build_report" and conv.raised:
                inp["method_notes"] = (inp.get("method_notes", "") + " Hypotheses added to the plan by people ("
                                       + ", ".join(h["id"] for h in conv.raised) + ") were each checked in their own "
                                       "notebook cell.").strip()
            if name == "build_report" and answered:
                asked = "; ".join(f"{_author(n['text'])}: \"{_note_text(n['text'])[:160]}\"" for n in answered)
                inp["method_notes"] = (inp.get("method_notes", "") + " Also checked during the run, as asked by the "
                                       f"people following it ({asked}); see the notebook cells added for each note.").strip()
            out.append((name, inp))
        return out

    def _figure(self, conv: Conversation, e: str) -> str:
        if e == "figure" or re.fullmatch(r"figures/c\d+_\d+\.png", e):
            return conv.script_figures[-1] if conv.script_figures else e
        return e

    def _plan(self, conv: Conversation, plan: dict) -> dict:
        plan = copy.deepcopy(plan)
        ups = conv.upload_tables
        if ups and not conv.follow_up:
            plan["hypotheses"][-1]["datasets"] = sorted(set(plan["hypotheses"][-1]["datasets"]) | set(ups))
            plan["notes"] = (plan.get("notes", "") + f" Also looks at the uploaded file ({', '.join(ups)}) for anything "
                             "that lines up with the yield drop.").strip()
        for n in conv.notes:  # every note so far, answered or being answered now
            text = _note_text(n["text"])
            top = max(int(h["id"][1:]) for h in plan["hypotheses"] if h["id"][1:].isdigit())
            plan["hypotheses"].append({
                "id": f"H{top + 1}",
                "cause": f"Raised by {_author(n['text'])}: {text[:200]}",
                "method": f"{_check_for(text)[0]}, then compare scope against baseline",
                "datasets": ["quality_lots", "historian_line2"], "source": f"note from {_author(n['text'])}"})
        return plan

    # answering a note
    def _react(self, conv: Conversation, note: dict) -> list:
        text, who = _note_text(note["text"]), _author(note["text"])
        if self.phase == "plan":
            plan_turn = next(t for t in self._script(conv) if any(n == "submit_plan" for n, _ in t))
            submit = next(inp for n, inp in plan_turn if n == "submit_plan")
            revised = self._plan(conv, submit)
            return [("text", f"**{who} asked:** {text}\n\nI added this to the plan as its own hypothesis, with a check "
                             "against the baseline, and kept the rest of the plan as it was."),
                    ("submit_plan", revised)]
        title, code = _check_for(text)
        about = " about that notebook cell" if "They wrote it about these notebook cells" in note["text"] else ""
        return [("text", f"**{who} wrote{about}:** {text}\n\nGood point. Adding a check to the notebook: "
                         f"**{title.lower()}**. I'll note it in the report."),
                ("run_python", {"code": code, "purpose": f"{title} (asked by {who})"})]

    def _upload_turn(self, conv: Conversation) -> list:
        table = conv.upload_tables[0]
        qid = "q4"  # the upload turn comes right after the three queries of the first turn
        return [("text", f"You attached a file to this question. Loading **{table}** to see whether it lines up "
                         "with the lots."),
                ("run_query", {"sql": f"select * from ds.{table} limit 5000", "purpose": "The file attached to this question"}),
                ("run_python", {"code": PREAMBLE + UPLOAD.replace("{qid}", qid), "purpose": "What the attached file shows, joined to the lots"})]


def _check_for(text: str) -> tuple[str, str]:
    low = text.lower()
    for pattern, title, code in CHECKS:
        if re.search(pattern, low):
            return title, code
    return DEFAULT_CHECK


class DemoReviewer:
    """The scripted independent review, for the main or the follow-up investigation."""

    name = "scripted-reviewer"

    def create(self, *, system: str, tools: list[dict], messages: list[dict]) -> dict:
        text = json.dumps(messages)
        review = FOLLOW_UP_REVIEW if FOLLOW_UP_PLAN["metric"] in text else script.REVIEW
        return {"content": [{"type": "text", "text": json.dumps(review)}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0}}
