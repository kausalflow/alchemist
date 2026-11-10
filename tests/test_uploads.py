"""Uploaded data files: conversion, a run that queries one, access, and the API."""

import datetime as dt
import os

import pandas as pd
import pytest
from openpyxl import Workbook

from alchemist import uploads
from alchemist.access import AccessDenied
from alchemist.agent import ScriptedModel
from alchemist.paths import read_json
from alchemist.run import ScriptedReviewer, approve, ask, replay
from alchemist.workspace import project_dir


def lims_workbook(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Lab results"
    ws.append(["Line 2 LIMS export"])
    ws.append([])
    ws.append(["Lot", "Sampled", "Viscosity (mPa s)", "MFI [g/10min]", "Operator", "Pass"])
    for i in range(12):
        ws.append([f"L{i:02d}", dt.datetime(2026, 8, 1 + i, 8), "<0.1" if i == 3 else 1200 + i, 3.5 + i / 10,
                   f"op{i % 3}", i % 2 == 0])
    sp = wb.create_sheet("Setpoints")
    sp.append(["Tag", "Value"])
    sp.append(["", "degC"])
    sp.append(["TT-204", 215.0])
    sp.append(["TT-205", 220])
    wb.create_sheet("Empty")
    wb.save(path)
    return path


# ------------------------------------------------------------------ conversion

def test_workbook_sheets_become_tables(tmp_path):
    rec = uploads.save(tmp_path / "proj", filename="lims.xlsx", source=lims_workbook(tmp_path / "lims.xlsx"), user="ana")
    tables = {t["name"]: t for t in rec["tables"]}
    assert set(tables) == {"up_lims_lab_results", "up_lims_setpoints"}
    assert rec["skipped"] == [{"sheet": "Empty", "reason": "no data"}]
    lab = tables["up_lims_lab_results"]
    assert lab["header_row"] == 3 and lab["first_data_row"] == 4 and lab["rows"] == 12
    cols = {c["name"]: c for c in lab["columns"]}
    assert cols["viscosity"]["unit"] == "mPa s" and cols["viscosity"]["type"] == "DOUBLE"
    assert cols["viscosity"]["original"] == "Viscosity (mPa s)"
    assert cols["mfi"]["unit"] == "g/10min" and cols["sampled"]["type"] == "TIMESTAMP"
    assert cols["pass"]["type"] == "BOOLEAN" and "viscosity__raw" in cols
    assert any("'<0.1'" in n for n in lab["notes"]) and any("above the header" in n for n in lab["notes"])
    sp = tables["up_lims_setpoints"]
    assert sp["units_row"] == 2 and {c["name"]: c for c in sp["columns"]}["value"]["unit"] == "degC"
    # The original is kept unchanged, with its hash.
    root = uploads.uploads_dir(tmp_path / "proj") / rec["id"]
    assert (root / "original" / "lims.xlsx").read_bytes() == (tmp_path / "lims.xlsx").read_bytes()
    df = pd.read_parquet(root / "tables" / "up_lims_lab_results" / "data.parquet")
    assert df["viscosity"].isna().sum() == 1 and df.loc[3, "viscosity__raw"] == "<0.1"


def test_european_csv(tmp_path):
    f = tmp_path / "Ofen 3.csv"
    f.write_bytes("Zeit;Temperatur (°C);Druck\n01.08.2026 08:00;215,5;1,2\n02.08.2026 08:00;216,1;1,4\n"
                  "13.08.2026 08:00;214,0;1,3\n".encode("cp1252"))
    rec = uploads.save(tmp_path / "proj", filename=f.name, source=f, user="ana")
    t = rec["tables"][0]
    assert t["name"] == "up_ofen_3" and t["sheet"] is None
    df = pd.read_parquet(uploads.uploads_dir(tmp_path / "proj") / rec["id"] / "tables" / t["name"] / "data.parquet")
    assert list(df["zeit"].dt.month) == [8, 8, 8] and list(df["zeit"].dt.day) == [1, 2, 13]
    assert df["temperatur"].tolist() == [215.5, 216.1, 214.0] and df["druck"].tolist() == [1.2, 1.4, 1.3]
    assert any("decimal separator" in n for n in t["notes"]) and any("day before month" in n for n in t["notes"])


def test_names_are_unique_in_a_project(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("x,y\n1,2\n")
    pdir = tmp_path / "proj"
    first = uploads.save(pdir, filename="a.csv", source=f, user="ana", taken=uploads.table_names(pdir))
    second = uploads.save(pdir, filename="a.csv", source=f, user="ana", taken=uploads.table_names(pdir))
    assert first["tables"][0]["name"] == "up_a" and second["tables"][0]["name"] == "up_a_2"


@pytest.mark.parametrize("name", ["data.parquet", "notes.docx", "x.exe"])
def test_other_file_types_are_refused(name):
    with pytest.raises(uploads.UploadError):
        uploads.file_format(name)


def test_damaged_workbook_is_refused(tmp_path):
    f = tmp_path / "bad.xlsx"
    f.write_bytes(b"not a zip")
    with pytest.raises(uploads.UploadError):
        uploads.save(tmp_path / "proj", filename="bad.xlsx", source=f, user="ana")


def test_file_name_cannot_leave_the_upload_folder():
    assert uploads.safe_name("../../etc/passwd") == "passwd"
    assert uploads.safe_name("C:\\Users\\x\\..\\data.xlsx") == "data.xlsx"


# ------------------------------------------------------------------ a run over an uploaded file

PLAN = {
    "metric": "Viscosity of Line 2 lots",
    "scope": {"plants": ["P01"]},
    "hypotheses": [{"id": "H1", "cause": "Viscosity drifted", "method": "Mean viscosity of the uploaded lots",
                    "datasets": ["up_lims_lab_results"], "source": "uploaded LIMS export"}],
}
SQL = "select * from ds.up_lims_lab_results order by lot"


def analysis_turns():
    return [
        [("search_catalog", {"query": "viscosity"})],
        [("run_query", {"sql": SQL, "purpose": "Uploaded lab results"})],
        [("record_finding", {"hypothesis": "H1", "statement": "Mean viscosity of the uploaded lots.",
                             "value_expression": "round(df_q1['viscosity'].mean(), 2)", "unit": "mPa s",
                             "verdict": "supported", "confidence": "medium", "evidence": ["q1"]})],
        [("finish_analysis", {"summary": "Mean viscosity computed from the uploaded file."})],
        [("build_report", {"title": "Uploaded viscosity", "conclusion": "Mean viscosity was {{F1}}.",
                           "top_causes": [{"cause": "Drift", "confidence": "medium", "finding": "F1"}],
                           "ruled_out": [], "next_check": "None.", "method_notes": "Mean of the uploaded values.",
                           "limitations": ["One value was '<0.1' and is left out."]})],
    ]


@pytest.fixture(scope="module")
def uploaded(svc, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("up")
    pdir = project_dir(svc.settings.workspace_root, "quality", "uploads-test")
    rec = uploads.save(pdir, filename="lims.xlsx", source=lims_workbook(tmp / "lims.xlsx"), user="eng_p1", plant="P01")
    uploads.set_exposure(pdir, rec["id"], "up_lims_lab_results", "operator", "never_to_model")
    return rec


@pytest.fixture(scope="module")
def upload_run(svc, uploaded):
    out = ask(svc, user="eng_p1", workspace="quality", project="uploads-test", question="Did viscosity drift?",
              scope={"plants": ["P01"]}, upload_ids=[uploaded["id"]],
              model=ScriptedModel([[("search_catalog", {"query": "viscosity"})], [("submit_plan", PLAN)]]))
    assert out["status"] == "awaiting_approval", out
    return approve(svc, out["run_id"], user="eng_p1", model=ScriptedModel(analysis_turns()),
                   reviewer=ScriptedReviewer({"ok": True, "issues": [], "notes": "fine"}))


def test_run_queries_the_uploaded_table(upload_run, uploaded):
    assert upload_run["status"] == "completed", upload_run
    run = upload_run["path"]
    f1 = read_json(os.path.join(run, "findings.json"))["findings"][0]
    assert f1["value"] == round(pd.Series([1200 + i for i in range(12) if i != 3]).mean(), 2)
    views = read_json(os.path.join(run, "inputs", "views.json"))
    v = views["up_lims_lab_results"]
    assert v["upload"]["sha256"] == uploaded["file"]["sha256"] and v["upload"]["header_row"] == 3
    manifest = read_json(os.path.join(run, "manifest.json"))
    assert {u["table"] for u in manifest["uploads"]} == {"up_lims_lab_results", "up_lims_setpoints"}
    plan_log = open(os.path.join(run, "logs", "plan_transcript.jsonl")).read()
    assert "Files the user uploaded" in plan_log and "viscosity__raw" in plan_log


def test_hidden_upload_column_never_reaches_the_model(upload_run):
    run = upload_run["path"]
    q = read_json(os.path.join(run, "inputs", "queries.json"))["q1"]
    snap = pd.read_parquet(os.path.join(run, q["snapshot"]))
    assert "operator" not in snap.columns and "viscosity__raw" in snap.columns
    steps = open(os.path.join(run, "logs", "transcript.jsonl")).read()
    assert "op0" not in steps and "op1" not in steps


def test_replay_of_upload_run_matches(svc, upload_run):
    res = replay(svc, upload_run["run_id"], user="eng_p1")
    assert res["status"] == "completed", res


def test_upload_of_another_plant_cannot_be_attached(svc, tmp_path):
    pdir = project_dir(svc.settings.workspace_root, "quality", "uploads-test")
    f = tmp_path / "p2.csv"
    f.write_text("a,b\n1,2\n")
    rec = uploads.save(pdir, filename="p2.csv", source=f, user="eng_p2", plant="P02",
                       taken=uploads.table_names(pdir))
    with pytest.raises(AccessDenied):
        ask(svc, user="eng_p1", workspace="quality", project="uploads-test", question="probe probe",
            scope={"plants": ["P01"]}, upload_ids=[rec["id"]], model=ScriptedModel([]))
    with pytest.raises(ValueError):
        ask(svc, user="eng_p1", workspace="quality", project="uploads-test", question="probe probe",
            scope={"plants": ["P01"]}, upload_ids=["0" * 26], model=ScriptedModel([]))


# ------------------------------------------------------------------ API

@pytest.fixture(scope="module")
def client(svc):
    from fastapi.testclient import TestClient

    from alchemist.api.app import create_app, services_factory_for
    from alchemist.api.jobs import LocalLauncher

    os.environ["ALC_API_MODEL"] = "scripted"  # no model calls from the planning step the question starts
    factory = services_factory_for(svc.settings)
    app = create_app(svc.settings, launcher=LocalLauncher(factory), services_factory=factory, web_dist="/nonexistent")
    with TestClient(app) as c:
        yield c
    os.environ.pop("ALC_API_MODEL", None)


def H(user):
    return {"X-Alchemist-User": user}


def test_api_upload_preview_and_hide(client, tmp_path):
    body = lims_workbook(tmp_path / "lims.xlsx").read_bytes()
    url = "/api/projects/quality/api-uploads/uploads"
    r = client.post(url, params={"filename": "LIMS export.xlsx", "plant": "P01", "time_zone": "Europe/Berlin"},
                    content=body, headers=H("eng_p1"))
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["file"]["name"] == "LIMS export.xlsx" and rec["time_zone"] == "Europe/Berlin"
    uid = rec["id"]
    got = client.get(f"{url}/{uid}", headers=H("eng_p1")).json()
    assert got["previews"]["up_lims_export_lab_results"]["head"][0]["lot"] == "L00"
    assert client.get(f"{url}/{uid}/original", headers=H("eng_p1")).content == body
    # A P02 user in the same workspace does not see P01 data, and may not change it.
    assert client.get(url, headers=H("eng_p2")).json() == []
    assert client.get(f"{url}/{uid}", headers=H("eng_p2")).status_code == 404
    r = client.put(f"{url}/{uid}/exposure", headers=H("eng_p1"),
                   json={"table": "up_lims_export_lab_results", "column": "operator", "exposure": "never_to_model"})
    assert r.status_code == 200
    cols = {c["name"]: c for c in r.json()["tables"][0]["columns"]}
    assert cols["operator"]["exposure"] == "never_to_model"
    # Attaching it to a question records it there.
    r = client.post("/api/questions", headers=H("eng_p1"), json={
        "workspace": "quality", "project": "api-uploads", "question": "What does the upload say?",
        "scope": {"plants": ["P01"]}, "uploads": [uid]})
    assert r.status_code == 201, r.text
    run = client.get(f"/api/runs/{r.json()['run_id']}", headers=H("eng_p1")).json()
    assert run["uploads"][0]["id"] == uid and run["uploads"][0]["tables"][0]["header_row"] == 3
    client.post(f"/api/runs/{r.json()['run_id']}:stop", headers=H("eng_p1"))


def test_api_refuses_bad_uploads(client):
    url = "/api/projects/quality/api-uploads/uploads"
    assert client.post(url, params={"filename": "x.exe"}, content=b"MZ", headers=H("eng_p1")).status_code == 415
    assert client.post(url, params={"filename": "x.csv"}, content=b"", headers=H("eng_p1")).status_code == 422
    assert client.post(url, params={"filename": "x.xlsx"}, content=b"junk", headers=H("eng_p1")).status_code == 422
    assert client.post(url, params={"filename": "x.csv", "plant": "P02"}, content=b"a\n1\n",
                       headers=H("eng_p1")).status_code == 403
    assert client.post("/api/projects/nope/p/uploads", params={"filename": "x.csv"}, content=b"a\n1\n",
                       headers=H("eng_p1")).status_code == 403
    r = client.post("/api/questions", headers=H("eng_p1"), json={
        "workspace": "quality", "project": "api-uploads", "question": "What does the upload say?",
        "scope": {"plants": ["P01"]}, "uploads": ["0" * 26]})
    assert r.status_code == 422


def test_csv_with_a_title_line_above_the_table(tmp_path):
    f = tmp_path / "lims.csv"
    lines = ["LIMS export, Line 2", ""] + ["Lot;Sampled;Viscosity (mPa s)"]
    lines += [f"L{i};0{1 + i}.08.2026 06:00;{'<0.1' if i == 2 else f'11{i},5'}" for i in range(8)]
    f.write_text("\n".join(lines) + "\n")
    t = uploads.save(tmp_path / "proj", filename=f.name, source=f, user="ana")["tables"][0]
    assert t["header_row"] == 3 and t["rows"] == 8
    cols = {c["name"]: c for c in t["columns"]}
    assert cols["viscosity"]["type"] == "DOUBLE" and cols["viscosity"]["unit"] == "mPa s" and "viscosity__raw" in cols
