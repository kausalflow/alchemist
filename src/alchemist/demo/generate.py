"""Synthetic plant data with a planted root cause, for demos and tests.

Plant P01, Line 2, 13 Jul to 9 Aug 2026. From 3 Aug most lots run on resin lot
R-118 (low melt flow), which lowers melt temperature by about 2 °C and first-pass
yield by about 5 points. Line speed and recipe do not change. Sensor TT-204 has a
six-hour gap on 5 Aug. Each dataset's metadata file uses a different layout, as
real owners' files do.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

START, END = pd.Timestamp("2026-07-13"), pd.Timestamp("2026-08-10")
R118_FROM = pd.Timestamp("2026-08-03")
RESIDENCE_MIN = 45


def _resin_for(ts: pd.Timestamp, rng) -> str:
    if ts >= R118_FROM:
        return "R-118" if rng.random() < 0.75 else "R-117"
    week = (ts - START).days // 7
    return ["R-115", "R-116", "R-117"][min(week, 2)]


def generate(home: Path, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    lake = home / "lake"

    # ---- quality lots (P01): one lot every 4 hours
    starts = pd.date_range(START, END - pd.Timedelta(hours=4), freq="4h")
    crews = ["A", "B", "C"]
    lots = []
    for i, s in enumerate(starts):
        resin = _resin_for(s, rng)
        fpy = 96.0 + rng.normal(0, 0.9) - (4.9 if resin == "R-118" else 0.0)
        lots.append({"lot_id": f"L2-{s:%m%d}-{i % 6 + 1}", "line": "L2", "start_ts": s, "end_ts": s + pd.Timedelta(hours=4),
                     "resin_lot": resin, "recipe_version": "RX-7.2", "crew": crews[(i // 3) % 3],
                     "operator_name": ["Jane Roe", "Max Mustermann", "Ana Silva"][(i // 3) % 3],
                     "first_pass_yield_pct": round(min(fpy, 99.9), 2)})
    lots_df = pd.DataFrame(lots)
    qdir = lake / "plant=P01" / "quality" / "lots"
    qdir.mkdir(parents=True, exist_ok=True)
    lots_df.to_parquet(qdir / "lots.parquet", index=False)
    (qdir / "metadata.json").write_text(json.dumps({
        "summary": "Finished lots of Line 2 with first-pass yield from inline inspection and the lab.",
        "granularity": "one row per finished lot (about 4 hours of production)",
        "tz": "UTC",
        "timestamp_meaning": "start_ts/end_ts are when the lot left the extruder die",
        "join_on": ["line", "lot_id"],
        "fields": {
            "lot_id": {"description": "lot identifier"},
            "start_ts": {"description": "lot start at the die (UTC)"},
            "end_ts": {"description": "lot end at the die (UTC)"},
            "resin_lot": {"description": "resin lot fed to the hopper"},
            "recipe_version": {"description": "MES recipe version"},
            "crew": {"description": "shift crew", "pseudonymize": True},
            "operator_name": {"description": "lead operator", "pii": True},
            "first_pass_yield_pct": {"description": "share of output passing first inspection", "units": "%", "min": 0, "max": 100},
        },
    }, indent=2))

    # ---- historian, Line 2 (P01): 1-minute averages, partitioned by day
    ts = pd.date_range(START - pd.Timedelta(hours=1), END, freq="min", inclusive="left")
    lot_at = pd.merge_asof(pd.DataFrame({"t": ts + pd.Timedelta(minutes=RESIDENCE_MIN)}),
                           lots_df[["start_ts", "resin_lot"]].rename(columns={"start_ts": "t"}), on="t")
    r118 = (lot_at["resin_lot"] == "R-118").to_numpy()
    temp = 230.5 + rng.normal(0, 0.6, len(ts)) - 2.0 * r118
    speed = 42.0 + rng.normal(0, 0.25, len(ts))
    pressure = 185 + rng.normal(0, 2.0, len(ts)) + 4.0 * r118
    hist = pd.DataFrame({"ts": ts, "line": "L2", "barrel_temp_c": temp.round(2), "line_speed_mpm": speed.round(2),
                         "melt_pressure_bar": pressure.round(1)})
    gap = (hist.ts >= "2026-08-05 08:00") & (hist.ts < "2026-08-05 14:00")
    hist.loc[gap, "barrel_temp_c"] = np.nan
    hroot = lake / "plant=P01" / "historian" / "line2"
    for day, part in hist.groupby(hist.ts.dt.date):
        d = hroot / f"date={day}"
        d.mkdir(parents=True, exist_ok=True)
        part.to_parquet(d / "part-0.parquet", index=False)
    (hroot / "_meta.yaml").write_text(yaml.safe_dump({
        "dataset": "Line 2 historian extract",
        "description": "Process tags of extrusion Line 2 from the plant historian, one-minute averages.",
        "owner": "process data team",
        "sampling": "1 minute averages",
        "timezone": "UTC",
        "keys": ["line", "ts"],
        "tags": [
            {"tag": "ts", "desc": "start of the one-minute interval (UTC)"},
            {"tag": "line", "desc": "line code"},
            {"tag": "barrel_temp_c", "desc": "barrel zone 3 melt temperature, sensor TT-204", "eng_unit": "degC", "range": [180, 260]},
            {"tag": "line_speed_mpm", "desc": "haul-off speed", "eng_unit": "m/min", "range": [0, 80]},
            {"tag": "melt_pressure_bar", "desc": "melt pressure before the die, PT-210", "eng_unit": "bar", "range": [0, 400]},
        ],
        "notes": ["TT-204 drops out for a few hours on some days; gaps are null values, not zeros."],
    }, sort_keys=False))

    # ---- resin receipts (P01): CSV with a minimal metadata file
    mdir = lake / "plant=P01" / "materials"
    mdir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"resin_lot": "R-115", "supplier": "PolyCo", "received_date": "2026-07-10", "cert_mfi_g10min": 2.1},
        {"resin_lot": "R-116", "supplier": "PolyCo", "received_date": "2026-07-17", "cert_mfi_g10min": 2.0},
        {"resin_lot": "R-117", "supplier": "PolyCo", "received_date": "2026-07-24", "cert_mfi_g10min": 2.1},
        {"resin_lot": "R-118", "supplier": "ResinWorks", "received_date": "2026-07-31", "cert_mfi_g10min": 1.4},
    ]).to_csv(mdir / "resin_lots.csv", index=False)
    (mdir / "_meta.yml").write_text("desc: Resin lot receipts with supplier certificate values\n")

    # ---- a second plant (P02) that P01 users must not read
    p2 = lake / "plant=P02" / "historian" / "line5" / "date=2026-08-01"
    p2.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ts": pd.date_range("2026-08-01", periods=60, freq="min"), "line": "L5",
                  "barrel_temp_c": 221.0}).to_parquet(p2 / "part-0.parquet", index=False)
    (lake / "plant=P02" / "historian" / "line5" / "_meta.yaml").write_text("description: Line 5 historian (plant P02)\ntimezone: UTC\n")

    # ---- knowledge
    kn = home / "knowledge"
    (kn / "structured").mkdir(parents=True, exist_ok=True)
    (kn / "glossary.md").write_text("---\nid: GLOSSARY\ntitle: Plant glossary\nversion: 5\nplant: global\nreview_date: 2027-01-01\n---\n"
                                    "# Quality\nWhen engineers say quality on an extrusion line they mean first-pass yield (FPY): the share of output passing first inspection.\n"
                                    "# MFI\nMelt flow index of a resin lot, g/10 min, from the supplier certificate. Lower MFI means a more viscous melt.\n")
    (kn / "FMEA-L2.md").write_text("---\nid: FMEA-L2\ntitle: FMEA extrusion Line 2\nversion: 4\nplant: P01\nreview_date: 2027-03-01\n---\n"
                                   "# Failure mode: voids and surface defects lower first-pass yield\n"
                                   "Causes ranked by occurrence: resin lot variation (MFI outside the usual band), low melt temperature, "
                                   "line speed above the validated range, recipe change, crew practice at start-up, sensor fault (TT-204, PT-210).\n"
                                   "# Detection\nInline inspection after the die; lab test per lot.\n")
    (kn / "SOP-114.md").write_text("---\nid: SOP-114\ntitle: Melt temperature window, Line 2\nversion: 3\nplant: P01\nreview_date: 2027-02-01\n---\n"
                                   "# Melt temperature window\nBarrel zone 3 melt temperature must stay within 228 to 234 degC. "
                                   "Below 229 degC the melt is not fully homogenised, which causes voids and lowers first-pass yield.\n"
                                   "# Low-MFI resin\nA resin lot with low melt flow index needs more energy to reach the same melt temperature; "
                                   "at unchanged heater settings the melt runs cooler.\n")
    (kn / "PI-2025-07.md").write_text("---\nid: PI-2025-07\ntitle: Past investigation, yield drop Line 2, July 2025\nversion: 1\nplant: P01\nreview_date: 2026-06-01\n---\n"
                                      "# Conclusion\nA five-point first-pass yield drop was traced to a resin lot with low MFI from a new supplier; "
                                      "melt temperature ran about two degrees low. Raising zone 3 setpoint restored yield.\n")
    (kn / "structured" / "line2_process.yaml").write_text(yaml.safe_dump({
        "plant": "P01", "version": 3, "line": "L2", "residence_time_min": RESIDENCE_MIN,
        "melt_temp_window_c": [228, 234], "validated_line_speed_mpm": [38, 46]}, sort_keys=False))

    # ---- access groups
    (home / "access.yaml").write_text(yaml.safe_dump({
        "plants": ["P01", "P02"],
        "workspaces": {"quality": {"plants": ["P01", "P02"]}},
        "users": {
            "eng_p1": {"plants": ["P01"], "workspaces": ["quality"]},
            "eng_p2": {"plants": ["P02"], "workspaces": ["quality"]},
            "data_admin": {"admin": True, "global": True, "workspaces": ["quality"]},
        },
    }, sort_keys=False))

    return {
        "historian_line2": dict(s3_prefix=str(hroot), file_glob="date=*/*.parquet", file_format="parquet", plant="P01",
                                metadata_uri=str(hroot / "_meta.yaml"), owner="process data team"),
        "quality_lots": dict(s3_prefix=str(qdir), file_glob="*.parquet", file_format="parquet", plant="P01",
                             metadata_uri=str(qdir / "metadata.json"), owner="quality lab", sensitivity="confidential"),
        "resin_lots": dict(s3_prefix=str(mdir), file_glob="*.csv", file_format="csv", plant="P01",
                           metadata_uri=str(mdir / "_meta.yml"), owner="purchasing"),
        "historian_line5": dict(s3_prefix=str(lake / "plant=P02" / "historian" / "line5"), file_glob="date=*/*.parquet",
                                file_format="parquet", plant="P02",
                                metadata_uri=str(lake / "plant=P02" / "historian" / "line5" / "_meta.yaml"), owner="P02 data"),
    }
