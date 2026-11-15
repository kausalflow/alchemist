"""Documents added to a project (PDF, Word, PowerPoint, text): reading them, a question that searches them, the API."""

import json
import os
import zipfile

import pytest

from alchemist import documents, uploads
from alchemist.agent import ScriptedModel
from alchemist.paths import as_path, read_json, read_yaml
from alchemist.run import ask
from alchemist.workspace import project_dir

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'


def make_docx(path, blocks):
    """A minimal .docx: blocks are ("h1", text), ("p", text) or ("table", [[cells]])."""
    body = []
    for kind, val in blocks:
        if kind == "table":
            rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r)
                           + "</w:tr>" for r in val)
            body.append(f"<w:tbl>{rows}</w:tbl>")
        else:
            style = f'<w:pPr><w:pStyle w:val="Heading{kind[1]}"/></w:pPr>' if kind.startswith("h") else ""
            body.append(f"<w:p>{style}<w:r><w:t>{val}</w:t></w:r></w:p>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f'<?xml version="1.0"?><w:document {W}><w:body>{"".join(body)}</w:body></w:document>')
    return path


def make_pptx(path, slides):
    with zipfile.ZipFile(path, "w") as z:
        for i, texts in enumerate(slides, 1):
            paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
            z.writestr(f"ppt/slides/slide{i}.xml", f'<?xml version="1.0"?><p:sld {A} xmlns:p="x"><a:txBody>{paras}</a:txBody></p:sld>')
    return path


def make_pdf(path, pages, scanned=False):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    with matplotlib.rc_context({"pdf.fonttype": 42}), PdfPages(path) as pdf:
        for text in pages:
            fig = plt.figure(figsize=(8.27, 11.69))
            if scanned:
                fig.figimage([[0.2, 0.8], [0.8, 0.2]], resize=False)
            else:
                fig.text(0.1, 0.9, text, va="top", wrap=True)
            pdf.savefig(fig)
            plt.close(fig)
    return path


SOP = [("h1", "SOP 12 Extruder start-up"), ("p", "Heat zones 1 to 4 to 215 degC before feeding resin."),
       ("h2", "Melt temperature"), ("p", "If melt temperature falls below 205 degC voids appear in the film."),
       ("table", [["Zone", "Setpoint"], ["Z1", "210"], ["Z2", "215"]])]


def test_word_document_keeps_headings_and_tables(tmp_path):
    rec = documents.save(tmp_path / "p", filename="SOP-12.docx", source=make_docx(tmp_path / "SOP-12.docx", SOP),
                         user="ana")
    text = documents.text(tmp_path / "p", rec["id"])
    assert "# SOP 12 Extruder start-up" in text and "## Melt temperature" in text
    assert "Z2 | 215" in text
    assert rec["sections"] == ["SOP 12 Extruder start-up", "Melt temperature"]
    assert rec["file"]["format"] == "docx" and rec["added_by"] == "ana"
    root = documents.documents_dir(tmp_path / "p") / rec["id"]
    assert (root / "original" / "SOP-12.docx").read_bytes() == (tmp_path / "SOP-12.docx").read_bytes()


def test_pdf_pages_and_slides_become_sections(tmp_path):
    rec = documents.save(tmp_path / "p", filename="spec.pdf",
                         source=make_pdf(tmp_path / "spec.pdf", ["Film thickness spec 25 um plus minus 2",
                                                                 "Haze below 3 percent"]), user="ana")
    assert rec["pages"] == 2 and rec["sections"] == ["Page 1", "Page 2"]
    assert "Haze below 3 percent" in documents.text(tmp_path / "p", rec["id"])
    rec = documents.save(tmp_path / "p", filename="review.pptx",
                         source=make_pptx(tmp_path / "review.pptx", [["Q3 review"], ["Voids rose in August", "Cause open"]]),
                         user="ana")
    assert rec["pages"] == 2 and "## Slide 2\n\nVoids rose in August\nCause open" in documents.text(tmp_path / "p", rec["id"])


def test_scanned_pdf_and_old_formats_are_refused_with_advice(tmp_path):
    with pytest.raises(uploads.UploadError, match="scan"):
        documents.save(tmp_path / "p", filename="scan.pdf", source=make_pdf(tmp_path / "scan.pdf", ["", ""], scanned=True),
                       user="ana")
    with pytest.raises(uploads.UploadError, match=r"\.docx"):
        documents.file_format("old.doc")
    with pytest.raises(uploads.UploadError, match="Save Worksheet As"):
        uploads.file_format("lab.MTW")
    with pytest.raises(uploads.UploadError, match="Minitab"):
        uploads.file_format("study.mpx")
    assert documents.is_document("a.PDF") and documents.is_document("a.doc") and not documents.is_document("a.xlsx")


def test_long_sections_are_split_for_search():
    body = "## Page 1\n\n" + "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(10))
    ps = documents._passages({"name": "long.pdf", "sha256": "ab" * 32}, body)
    assert len(ps) > 2 and all(len(p["text"]) <= 1500 for p in ps)
    assert ps[0]["heading"] == "Page 1 (part 1)" and ps[0]["doc"] == "project-doc:long.pdf"


PLAN = {"metric": "Voids", "scope": {"plants": ["P01"]},
        "hypotheses": [{"id": "H1", "cause": "Cold melt", "method": "Mean", "datasets": ["quality_lots"]}]}


def test_question_searches_and_cites_project_documents(svc, tmp_path):
    pdir = project_dir(svc.settings.workspace_root, "quality", "docs-test")
    kept = documents.save(pdir, filename="SOP-12.docx", source=make_docx(tmp_path / "SOP-12.docx", SOP), user="eng_p1")
    gone = documents.save(pdir, filename="old.txt", source=_text(tmp_path / "old.txt", "Outdated melt note"), user="eng_p1")
    documents.remove(pdir, gone["id"], "eng_p1")
    out = ask(svc, user="eng_p1", workspace="quality", project="docs-test", question="Why did voids rise?",
              scope={"plants": ["P01"]},
              model=ScriptedModel([[("search_knowledge", {"query": "melt temperature voids"})], [("submit_plan", PLAN)]]))
    run = as_path(out["path"])
    q = read_yaml(run.parent.parent / "question.yaml")
    assert [d["id"] for d in q["documents"]] == [kept["id"]]
    log = (run / "logs" / "plan_transcript.jsonl").read_text()
    assert "SOP-12.docx" in log and "headings: SOP 12 Extruder start-up; Melt temperature" in log
    assert "below 205 degC voids appear" in log  # the search result
    cites = read_json(run / "logs" / "citations.json")
    assert any(c["doc"] == "project-doc:SOP-12.docx" and c["version"] == kept["file"]["sha256"][:12] for c in cites)


def _text(path, s):
    path.write_text(s)
    return path


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


def test_api_add_read_and_remove_documents(client, tmp_path):
    H = {"X-Alchemist-User": "eng_p1"}
    url = "/api/projects/quality/docs-api/documents"
    data = make_docx(tmp_path / "SOP.docx", SOP).read_bytes()
    r = client.post(url, params={"filename": "SOP.docx"}, content=data, headers=H)
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    assert [d["id"] for d in client.get(url, headers=H).json()] == [did]
    got = client.get(f"{url}/{did}", headers={"X-Alchemist-User": "eng_p2"}).json()
    assert "Melt temperature" in got["text"] and got["truncated"] is False
    assert client.get(f"{url}/{did}/original", headers=H).content == data
    r = client.post(url, params={"filename": "old.doc"}, content=b"x", headers=H)
    assert r.status_code == 415 and ".docx" in r.json()["detail"]
    r = client.post(url, params={"filename": "bad.pdf"}, content=b"not a pdf", headers=H)
    assert r.status_code == 422
    r = client.post("/api/projects/quality/docs-api/uploads", params={"filename": "lab.mtw"}, content=b"x", headers=H)
    assert r.status_code == 415 and "Save Worksheet As" in r.json()["detail"]
    assert client.delete(f"{url}/{did}", headers=H).json()["removed_by"] == "eng_p1"
    assert client.get(url, headers=H).json() == []
    assert client.get("/api/projects/maint/x/documents", headers=H).status_code in (403, 404)
