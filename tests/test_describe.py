"""The plain-words description of an uploaded file and its starter questions."""

import datetime as dt
import os

import pandas as pd
import pytest

from alchemist import describe, uploads


def batches_csv(path):
    rows = ["Batch,Date,Reactor,Temperature (degC),Pressure [bar],Yield (%),Operator"]
    for i in range(20):
        rows.append(f"B{i:03d},{dt.date(2026, 8, 1 + i)},R{1 + i % 3},{180 + i % 5},{2.0 + i / 20:.2f},"
                    f"{90 + (i % 7) / 2:.1f},op{i % 4}")
    path.write_text("\n".join(rows) + "\n")
    return path


def test_description_and_questions(tmp_path):
    rec = uploads.save(tmp_path / "proj", filename="batches.csv", source=batches_csv(tmp_path / "batches.csv"),
                       user="ana")
    got = describe.about(tmp_path / "proj", rec)
    text = got["tables"][0]["description"]
    assert text.startswith("20 rows and 7 columns.")
    assert "01 Aug 2026 to 20 Aug 2026" in text
    assert "3 Reactor (R1, R2, R3)" in text
    assert "Yield from 90 to 93 %" in text
    assert "Identifiers: Batch" in text
    qs = got["questions"]
    assert 3 <= len(qs) <= 4
    assert qs[0] == "Which Reactor has the lowest Yield, and is the difference real or just noise?"
    assert any("change between 01 Aug 2026 and 20 Aug 2026" in q for q in qs)
    assert any(q.startswith("What explains the variation in Yield? Check Temperature, Pressure") for q in qs)


def test_hidden_columns_are_left_out(tmp_path):
    pdir = tmp_path / "proj"
    rec = uploads.save(pdir, filename="batches.csv", source=batches_csv(tmp_path / "batches.csv"), user="ana")
    rec = uploads.set_exposure(pdir, rec["id"], rec["tables"][0]["name"], "operator", "never_to_model")
    got = describe.about(pdir, rec)
    assert "Operator" not in got["tables"][0]["description"]
    assert not any("Operator" in q for q in got["questions"])


def test_table_without_numbers_has_few_questions():
    df = pd.DataFrame({"tag": [f"T{i}" for i in range(40)], "area": ["A", "B"] * 20})
    info = describe.describe_table(df, [{"name": "tag"}, {"name": "area"}])
    assert describe.starter_questions(info) == []
    assert "Identifiers: tag" in " ".join(info["sentences"])


@pytest.fixture(scope="module")
def client(svc):
    from fastapi.testclient import TestClient

    from alchemist.api.app import create_app, services_factory_for
    from alchemist.api.jobs import LocalLauncher

    os.environ["ALC_API_MODEL"] = "scripted"
    factory = services_factory_for(svc.settings)
    app = create_app(svc.settings, launcher=LocalLauncher(factory), services_factory=factory, web_dist="/nonexistent")
    with TestClient(app) as c:
        yield c
    os.environ.pop("ALC_API_MODEL", None)


def test_api_about(client, tmp_path):
    url = "/api/projects/quality/describe/uploads"
    body = batches_csv(tmp_path / "b.csv").read_bytes()
    r = client.post(url, params={"filename": "b.csv", "plant": "P01"}, content=body,
                    headers={"X-Alchemist-User": "eng_p1"})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    got = client.get(f"{url}/{uid}/about", headers={"X-Alchemist-User": "eng_p1"}).json()
    assert got["questions"] and got["tables"][0]["description"].startswith("20 rows")
    # Someone who may not read P01 does not get it either.
    assert client.get(f"{url}/{uid}/about", headers={"X-Alchemist-User": "eng_p2"}).status_code == 404
