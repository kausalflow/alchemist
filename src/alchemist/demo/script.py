"""A scripted investigation of the demo question, played through the real agent loop.

It exercises every tool exactly as a model would, so the demo and the tests run
offline. With Bedrock access, `alchemist ask` uses Claude instead.
"""

from __future__ import annotations

QUESTION = "Why was quality low on Line 2 from 3 to 9 August 2026?"
SCOPE = {"line": "L2", "plants": ["P01"], "period": ["2026-08-03", "2026-08-09"]}

PLAN = {
    "metric": "First-pass yield (FPY), % of each lot passing first inspection, Line 2",
    "scope": {"line": "L2", "plants": ["P01"], "period": ["2026-08-03", "2026-08-09"],
              "baseline": ["2026-07-13", "2026-08-02"],
              "baseline_reason": "Three weeks just before the drop with no recipe change or shutdown on record"},
    "hypotheses": [
        {"id": "H1", "cause": "Resin lot change (R-118, new supplier, low MFI)", "method": "Compare FPY of R-118 lots with other lots in the same week; Welch t-test and effect size; check within each crew",
         "datasets": ["quality_lots", "resin_lots"], "source": "FMEA-L2, PI-2025-07"},
        {"id": "H2", "cause": "Low melt temperature", "method": "Align barrel zone 3 temperature to lots with the 45 min residence time; compare by resin lot against the SOP-114 window",
         "datasets": ["historian_line2", "quality_lots"], "source": "SOP-114, FMEA-L2"},
        {"id": "H3", "cause": "Line speed increase", "method": "Compare aligned line speed in scope vs baseline",
         "datasets": ["historian_line2"], "source": "FMEA-L2"},
        {"id": "H4", "cause": "Recipe change", "method": "Count recipe versions in scope not seen in the baseline",
         "datasets": ["quality_lots"], "source": "FMEA-L2"},
        {"id": "H5", "cause": "Sensor fault on TT-204", "method": "Data health check: gaps and implausible values before any test",
         "datasets": ["historian_line2"], "source": "FMEA-L2"},
        {"id": "H0", "cause": "No abnormality (normal variation)", "method": "Size of the period-vs-baseline difference against baseline lot-to-lot variation",
         "datasets": ["quality_lots"], "source": "general engineering knowledge"},
    ],
    "notes": "Crew is pseudonymised in the catalog; it is used only as a stratification variable.",
}

PLAN_TURNS = [
    [("search_catalog", {"query": "line 2 first pass yield lots resin"}),
     ("search_catalog", {"query": "line 2 historian barrel temperature line speed"}),
     ("search_knowledge", {"query": "low first-pass yield line 2 causes voids"})],
    [("search_knowledge", {"query": "melt temperature window resin MFI"})],
    [("submit_plan", PLAN)],
]

Q_LOTS = """
select lot_id, line, start_ts, end_ts, resin_lot, recipe_version, crew, first_pass_yield_pct
from ds.quality_lots
where line = 'L2' and start_ts >= timestamp '2026-07-13' and start_ts < timestamp '2026-08-10'
order by start_ts
"""

Q_PROC = """
select ts, line, barrel_temp_c, line_speed_mpm, melt_pressure_bar
from ds.historian_line2
where ts >= timestamp '2026-07-12 23:00' and ts < timestamp '2026-08-10'
order by ts
"""

Q_RESIN = "select resin_lot, supplier, received_date, cert_mfi_g10min from ds.resin_lots order by resin_lot"

HEALTH = """import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

proc = df_q2.copy()
expected = pd.date_range(proc.ts.min(), proc.ts.max(), freq="min")
print("minutes expected:", len(expected), " rows:", len(proc))
null_temp = proc.set_index("ts")["barrel_temp_c"].isna()
by_day = null_temp.groupby(null_temp.index.date).sum()
print("TT-204 null minutes by day:")
print(by_day[by_day > 0])
print(proc[["barrel_temp_c", "line_speed_mpm", "melt_pressure_bar"]].describe().round(2))
lots = df_q1.copy()
print("lots:", len(lots), " lots with missing yield:", int(lots.first_pass_yield_pct.isna().sum()))

hourly = proc.set_index("ts")["barrel_temp_c"].resample("h").mean()
gaps = null_temp.resample("h").sum()
fig, ax = plt.subplots(figsize=(9, 3.2))
ax.plot(hourly.index, hourly.values, lw=0.8)
for t in gaps[gaps > 0].index:  # hours with missing TT-204 minutes
    ax.axvspan(t, t + pd.Timedelta(hours=1), color="red", alpha=0.3, lw=0)
ax.axvline(pd.Timestamp("2026-08-03"), color="grey", ls="--", lw=1)
ax.set_ylabel("Barrel zone 3, degC")
ax.set_title(f"Data health: TT-204 hourly mean; {int(null_temp.sum())} missing minutes shaded red; scope from 3 Aug")
plt.tight_layout()
plt.show()"""

YIELD = """import matplotlib.pyplot as plt

lots["period"] = np.where(lots.start_ts >= "2026-08-03", "scope", "baseline")
scope, base = lots[lots.period == "scope"], lots[lots.period == "baseline"]
fx_period = alc.effect(scope.first_pass_yield_pct, base.first_pass_yield_pct)
print("scope vs baseline:", {k: round(v, 3) if isinstance(v, float) else v for k, v in fx_period.items()})
print("baseline lot-to-lot sd:", round(base.first_pass_yield_pct.std(), 2))

fig, ax = plt.subplots(figsize=(9, 3.6))
for resin, g in lots.groupby("resin_lot"):
    ax.scatter(g.start_ts, g.first_pass_yield_pct, s=14, label=resin)
ax.axvline(pd.Timestamp("2026-08-03"), color="grey", ls="--", lw=1)
ax.set_ylabel("First-pass yield, %")
ax.set_title("Line 2 first-pass yield per lot, coloured by resin lot")
ax.legend(ncol=5, fontsize=8)
plt.tight_layout()
plt.show()"""

RESIN = """in_scope = scope.copy()
is118 = in_scope.resin_lot == "R-118"
fx_resin = alc.effect(in_scope[is118].first_pass_yield_pct, in_scope[~is118].first_pass_yield_pct)
print("R-118 vs other lots, same week:", {k: round(v, 3) if isinstance(v, float) else v for k, v in fx_resin.items()})
print(in_scope.groupby(["crew", "resin_lot"]).first_pass_yield_pct.agg(["mean", "count"]).round(2))
print(df_q3)"""

ALIGN = """k = alc.knowledge("line2_process")
aligned = alc.align(lots, proc, start="start_ts", end="end_ts", ts="ts", residence_min=k["residence_time_min"],
                    values=["barrel_temp_c", "line_speed_mpm"], by=["line"])
aligned["period"] = np.where(aligned.start_ts >= "2026-08-03", "scope", "baseline")
sc = aligned[aligned.period == "scope"]
fx_temp = alc.effect(sc[sc.resin_lot == "R-118"].barrel_temp_c.dropna(), sc[sc.resin_lot != "R-118"].barrel_temp_c.dropna())
fx_speed = alc.effect(sc.line_speed_mpm, aligned[aligned.period == "baseline"].line_speed_mpm)
new_recipes = sorted(set(sc.recipe_version) - set(aligned[aligned.period == "baseline"].recipe_version))
lo, hi = k["melt_temp_window_c"]
print("melt temp, R-118 vs other lots in scope:", {k_: round(v, 3) if isinstance(v, float) else v for k_, v in fx_temp.items()})
print("share of R-118 lots below", lo + 1, "degC:", round((sc[sc.resin_lot == "R-118"].barrel_temp_c < lo + 1).mean(), 2))
print("line speed scope vs baseline:", {k_: round(v, 3) if isinstance(v, float) else v for k_, v in fx_speed.items()})
print("recipe versions new in scope:", new_recipes)

fig, ax = plt.subplots(figsize=(6, 3.6))
ax.scatter(aligned.barrel_temp_c, aligned.first_pass_yield_pct, c=(aligned.resin_lot == "R-118"), cmap="coolwarm", s=14)
ax.axvline(lo + 1, color="grey", ls="--", lw=1)
ax.set_xlabel("Barrel zone 3 temperature over the lot's process window, degC")
ax.set_ylabel("First-pass yield, %")
ax.set_title("Lower melt temperature goes with lower yield (red: R-118)")
plt.tight_layout()
plt.show()"""

CREW_CHECK = """within = in_scope.groupby("crew").apply(
    lambda g: g[g.resin_lot == "R-118"].first_pass_yield_pct.mean() - g[g.resin_lot != "R-118"].first_pass_yield_pct.mean())
print("R-118 effect within each crew (points):")
print(within.round(2))
print("non-R-118 lots in scope:", int((~is118).sum()))"""

ANALYSIS_TURNS = [
    [("run_query", {"sql": Q_LOTS, "purpose": "Lot yields with resin lot, recipe and crew for scope and baseline"}),
     ("run_query", {"sql": Q_PROC, "purpose": "One-minute process tags, including one hour before the first lot"}),
     ("run_query", {"sql": Q_RESIN, "purpose": "Resin lot certificates"})],
    [("run_python", {"code": HEALTH, "purpose": "Data health: gaps in TT-204 and implausible values before any test (H5)"})],
    [("run_python", {"code": YIELD, "purpose": "Size of the yield drop against the baseline (H0)"})],
    [("record_finding", {"hypothesis": "H0", "statement": "Drop in mean first-pass yield from the baseline to the scope week; far larger than baseline lot-to-lot variation, so not normal variation.",
                         "value_expression": "round(-fx_period['diff'], 2)", "unit": "percentage points", "verdict": "refuted",
                         "confidence": "high", "evidence": ["q1", "figures/c7_1.png"]})],
    [("run_python", {"code": RESIN, "purpose": "Compare R-118 lots with other lots in the same week (H1)"})],
    [("record_finding", {"hypothesis": "H1", "statement": "How much lower first-pass yield was on R-118 lots than on other lots in the same week.",
                         "value_expression": "round(-fx_resin['diff'], 2)", "unit": "percentage points", "verdict": "supported",
                         "confidence": "high", "evidence": ["q1", "q3", "FMEA-L2", "PI-2025-07"]})],
    [("run_python", {"code": ALIGN, "purpose": "Align process conditions to lots with the residence time; test temperature, speed and recipe (H2, H3, H4)"})],
    [("record_finding", {"hypothesis": "H2", "statement": "How much cooler the melt ran over the process window of R-118 lots than of other lots in the same week, toward the low edge of the SOP-114 window.",
                         "value_expression": "round(-fx_temp['diff'], 2)", "unit": "degC", "verdict": "supported",
                         "confidence": "medium", "evidence": ["q2", "figures/c11_1.png", "SOP-114"]}),
     ("record_finding", {"hypothesis": "H3", "statement": "Line speed in the scope week did not differ meaningfully from the baseline.",
                         "value_expression": "round(fx_speed['diff'], 2)", "unit": "m/min", "verdict": "refuted",
                         "confidence": "high", "evidence": ["q2"]}),
     ("record_finding", {"hypothesis": "H4", "statement": "No recipe version was used in the scope week that was not already used in the baseline.",
                         "value_expression": "len(new_recipes)", "unit": "new recipe versions", "verdict": "refuted",
                         "confidence": "high", "evidence": ["q1"]})],
    [("run_python", {"code": CREW_CHECK, "purpose": "Check the R-118 effect holds within each crew (confounding by crew)"})],
    [("finish_analysis", {"summary": "R-118 lots explain the yield drop; melt temperature ran low on those lots, consistent with low MFI. Speed and recipe unchanged; TT-204 gap on 5 Aug excluded."})],
    [("build_report", {
        "title": "Line 2 first-pass yield, 3 to 9 August 2026",
        "conclusion": "First-pass yield on Line 2 fell by {{F1}} against the 13 Jul to 2 Aug baseline. "
                      "The drop follows resin lot R-118: its lots yielded {{F2}} less than other lots in the same week, "
                      "and their melt ran {{F3}} cooler, which SOP-114 links to voids. "
                      "Line speed and recipe did not change.",
        "top_causes": [{"cause": "Resin lot R-118 (new supplier, low melt flow index)", "confidence": "high", "finding": "F2"},
                       {"cause": "Lower melt temperature on R-118 lots, the likely mechanism", "confidence": "medium", "finding": "F3"}],
        "ruled_out": ["line speed (F4)", "recipe change (F5)", "normal variation (F1)"],
        "next_check": "Check the R-118 supplier certificate and consider raising the zone 3 setpoint while R-118 is in use.",
        "method_notes": "Lots were joined to process conditions with the 45-minute residence time from structured knowledge. "
                        "Comparisons use Welch's t-test with Cohen's d; the resin effect was checked within each crew.",
        "limitations": ["Only a few non-R-118 lots ran in the scope week, so the size of the resin effect is less certain than its direction.",
                        "TT-204 has a six-hour gap on 5 August; those minutes are excluded from temperature averages."],
    })],
]

REVIEW = {
    "ok": True,
    "issues": [{"finding": "F2", "problem": "Only a handful of non-R-118 lots ran in the scope week.",
                "suggestion": "Keep high confidence on direction but state the uncertainty of the size."}],
    "notes": "Each value is computed in the cell cited. The resin effect holds within every crew, so crew does not confound it. "
             "The temperature mechanism is cited from SOP-114 and matches the past investigation PI-2025-07.",
}
