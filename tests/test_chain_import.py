"""Chains that ran outside Metalens: the register project's adjudicated claim graph (an
aggregate carrying its runs as a child) through the import endpoints, and a
``{passes, aggregate}`` payload whose aggregate cites the passes by reference."""
from __future__ import annotations

import copy
import json
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from paperlens import chain, preset_spec, presets, records, storage  # noqa: E402
from test_chain_extract import ADJ, RUNS, _db_ok, _pdf, _setup, _spec  # noqa: E402

GRAPH = json.load(open(os.path.join(_HERE, "snapshots", "chain", "dk_W3174613487.paperlens.json"), encoding="utf-8"))


def _graph_pdf() -> bytes:
    """A PDF whose pages print the snapshot's snippets where the snapshot says they are."""
    import fitz
    d = fitz.open()
    by_page: dict[int, list[str]] = {}
    for e in GRAPH["evidence"]:
        by_page.setdefault(int(e["page"]), []).append(e["snippet"])
    for n in range(1, max(by_page) + 1):
        pg = d.new_page()
        y = 80
        for s in dict.fromkeys(by_page.get(n, [])):
            for chunk in [s[i:i + 95] for i in range(0, len(s), 95)]:
                pg.insert_text((40, y), chunk, fontsize=8); y += 11
            y += 6
    out = d.tobytes(); d.close(); return out


def test_external_aggregate_imports_with_its_run_facts(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    import tempfile
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    spec = presets.load_all()["claim-graph-adjudication"]
    sid = preset_spec.schema_id(spec)
    sess = f"gi-{uuid.uuid4().hex[:6]}"; hdr = {"X-Session-Id": sess}
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        monkeypatch.setattr(storage, "get_store", lambda: store)
        c = TestClient(appmod.app)
        r = c.post("/api/ingest-pdf", files={"pdf": ("dk_W3174613487.pdf", _graph_pdf(), "application/pdf")},
                   data={"result": json.dumps(GRAPH, ensure_ascii=False), "schema_id": sid, "produced_by": "register pipeline"}, headers=hdr)
        assert r.status_code == 200, r.text
        doc = r.json()["document_id"]
        v = c.get(f"/api/documents/{doc}/view", headers=hdr).json()
        assert v["role"] == "final" and v["chain"] == {"kind": "external", "imported": True}
        recs = v["records"]
        assert len(recs) == 2 and recs[0]["chain"]["support"] == {"n": 3, "k": 3} and recs[0]["chain"]["kind"] == "external"
        assert [x["label"] for x in recs[0]["chain"]["runs"]] == ["stated_claims", "stated_claims_v3", "stated_claims_run2"]
        assert recs[0]["chain"]["runs"][0]["row_index"] == 0
        assert recs[1]["chain"]["flags"] == ["sign"]                             # the file's own flag
        assert "sign" in recs[1]["chain"]["candidates"]                           # the runs' signs disagree on edge 2
        assert all(len(c["runs"]) >= 1 for c in recs[1]["chain"]["candidates"]["sign"])
        assert len(v["pages"]) >= 4 and any(e["rect"] for e in v["evidence"]), "string pages were located"
        # the same file without a PDF: no pages, the facts intact
        r2 = c.post("/api/ingest", json={"result": GRAPH, "schema_id": sid}, headers=hdr)
        assert r2.status_code == 200, r2.text
        v2 = c.get(f"/api/documents/{r2.json()['document_id']}/view", headers=hdr).json()
        assert v2["pages"] == [] and v2["records"][0]["chain"]["support"] == {"n": 3, "k": 3}
        for did in (doc, r2.json()["document_id"]):
            records.delete_document(conn, did)
    conn.close()


def test_passes_and_aggregate_import_resolves_refs(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    import tempfile
    conn = records.connect(); records.init_db(conn)
    spec = _spec(); sid = _setup(conn, spec); sess = f"gi-{uuid.uuid4().hex[:6]}"
    payload = {"passes": RUNS, "aggregate": {**ADJ, "paper_metadata": RUNS[0]["paper_metadata"]}}
    assert chain.is_chain_payload(payload) and not chain.is_chain_payload(RUNS[0])
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        out = chain.import_chain(conn, payload, spec=spec, schema_id=sid, pdf_bytes=_pdf(), session_id=sess,
                                 owner_user_id=None, filename="chain.pdf", produced_by="outside", store=store)
        assert len(out["run_document_ids"]) == 3
        v = records.document_view(conn, out["document_id"], store=store)
        assert v["role"] == "final" and v["chain"]["imported"] is True and v["chain"]["n_runs"] == 3
        assert v["records"][1]["chain"]["support"] == {"n": 3, "k": 3}
        assert [x["value"] for x in v["records"][1]["chain"]["candidates"]["sign"]] == ["+", "-"]
        assert any(e["rect"] for e in v["evidence"]) and all(e["snippet"] for e in v["evidence"])
        assert records.list_documents(conn, session_id=sess) and len(records.list_documents(conn, session_id=sess)) == 1
        # without a PDF: persisted, grouped, nothing located
        out2 = chain.import_chain(conn, payload, spec=spec, schema_id=sid, pdf_bytes=None, session_id=sess,
                                  owner_user_id=None, filename=None, store=store)
        v2 = records.document_view(conn, out2["document_id"], store=store)
        assert v2["pages"] == [] and v2["records"][2]["chain"]["support"]["n"] == 1
        for did in (out["document_id"], out2["document_id"]):
            records.delete_document(conn, did)
        assert conn.execute("SELECT count(*) FROM extraction_document WHERE session_id = %s", (sess,)).fetchone()[0] == 0
    conn.close()


def test_passes_of_one_shape_behind_an_aggregate_of_another(monkeypatch) -> None:
    """The memo's picture: claim runs (register-claims) as passes, the merged graph of edges
    (claim-graph-adjudication) as the aggregate citing them by ref. Edges inherit the runs'
    quotes, support and candidates come from the runs, and the runs are filed under their
    own preset."""
    if not _db_ok():
        pytest.skip("no Postgres")
    import tempfile
    conn = records.connect(); records.init_db(conn)
    cspec = presets.load_all()["register-claims"]; csid = preset_spec.schema_id(cspec)
    gspec = presets.load_all()["claim-graph-adjudication"]; gsid = preset_spec.schema_id(gspec)
    for sp, si in ((cspec, csid), (gspec, gsid)):
        with conn.transaction():
            records.upsert_schema(conn, si, preset_spec.field_defs_for(sp))
    sess = f"gi-{uuid.uuid4().hex[:6]}"
    pm = {"title": "Edges from claims", "doi": None, "year": 2021, "authors": ["A"], "journal": None}

    def claim(cid, cause, effect, sign):
        return {"claim_id": cid, "anchor_id": "A1", "type": "edge", "statement": f"{cause} affects {effect}", "cause": cause,
                "effect": effect, "sign": sign, "anchor_quote": "Nonfatal shocks have no meaningful effects on spousal labor supply",
                "support": "explicit", "scope_setting": "Denmark", "scope_period": "1990-2010", "scope_population": "spouses",
                "scope_identification": "event study", "notes": None, "results": []}
    runs = [{"paper_metadata": pm, "claims": [claim("S1", "nonfatal shocks", "spousal labor supply", "0"), claim("S2", "fatal shocks", "spousal labor supply", "+")],
             "evidence": [{"snippet": "Nonfatal shocks have no meaningful effects on spousal labor supply", "page": 1, "source": None, "field": "claims[0]"},
                          {"snippet": "spouses increase their labor supply after a fatal event", "page": 1, "source": None, "field": "claims[1]"}]},
            {"paper_metadata": pm, "claims": [claim("S1", "severe nonfatal health shocks", "spousal labor supply", "0")],
             "evidence": [{"snippet": "Nonfatal shocks have no meaningful effects on spousal labor supply", "page": 1, "source": None, "field": "claims[0]"}]}]
    agg = {"paper_metadata": {**pm, "k": 2, "run_names": ["a", "b"]},
           "records": [{"edge_id": "V1->V2", "cause": "severe nonfatal health shocks", "effect": "spousal labor supply", "sign": "0",
                        "direction": "agreed", "support": "2/2", "tier": "unanimous", "kind": "causal", "design_basis": "identified",
                        "design": "event_study", "flags": None, "headline_cells": [], "neighbours": [],
                        "runs": [{"run": "a", "cause_wording": "nonfatal shocks", "effect_wording": "spousal labor supply", "run_sign": "0", "run_direction": "V1->V2", "claims": "S1"},
                                 {"run": "b", "cause_wording": "severe nonfatal health shocks", "effect_wording": "spousal labor supply", "run_sign": "0", "run_direction": "V1->V2", "claims": "S1"}]},
                       {"edge_id": "V3->V2", "cause": "fatal shocks", "effect": "spousal labor supply", "sign": "+", "direction": "agreed",
                        "support": "1/2", "tier": "backed", "kind": "causal", "design_basis": "identified", "design": "event_study",
                        "flags": "existence", "headline_cells": [], "neighbours": [],
                        "runs": [{"run": "a", "cause_wording": "fatal shocks", "effect_wording": "spousal labor supply", "run_sign": "+", "run_direction": "V3->V2", "claims": "S2"}]}],
           "evidence": [{"ref": ["r1.claims[0]", "r2.claims[0]"], "field": "records[0]"}, {"ref": "r1.claims[1]", "field": "records[1]"}]}
    import fitz
    d = fitz.open(); pg = d.new_page()
    pg.insert_text((40, 80), "Nonfatal shocks have no meaningful effects on spousal labor supply.", fontsize=9)
    pg.insert_text((40, 110), "In contrast, spouses increase their labor supply after a fatal event.", fontsize=9)
    pdf = d.tobytes(); d.close()
    with tempfile.TemporaryDirectory() as tmp:
        store = storage.LocalObjectStore(root=tmp)
        out = chain.import_chain(conn, {"passes": runs, "aggregate": agg}, spec=gspec, schema_id=gsid, pdf_bytes=pdf, session_id=sess,
                                 owner_user_id=None, filename="edges.pdf", produced_by="pipeline", store=store,
                                 passes_spec=cspec, passes_schema_id=csid)
        v = records.document_view(conn, out["document_id"], store=store)
        assert v["schema_id"] == gsid and v["chain"]["passes_schema_id"] == csid
        e0, e1 = v["records"]
        assert e0["chain"]["support"] == {"n": 2, "k": 2} and e1["chain"]["support"] == {"n": 1, "k": 2}
        assert "minority" in e1["chain"]["flags"]
        assert [x["value"] for x in e0["chain"]["candidates"]["cause"]] == ["nonfatal shocks", "severe nonfatal health shocks"]   # the runs' wordings
        quotes = {(e["field_path"], e["snippet"]) for e in v["evidence"]}
        assert ("records[0]", "Nonfatal shocks have no meaningful effects on spousal labor supply") in quotes
        assert ("records[1]", "spouses increase their labor supply after a fatal event") in quotes
        assert any(e["rect"] for e in v["evidence"])
        assert any(e["source"].endswith("(r1, r2)") for e in v["evidence"])
        for rid in out["run_document_ids"]:
            rv = records.document_view(conn, rid, store=store)
            assert rv["schema_id"] == csid and rv["read_only"] is True and rv["records"][0]["field_values"]["claim_id"] == "S1"
        assert e0["chain"]["trace"][0]["ref"] == "r1.claims[0]" and e0["chain"]["runs"][0]["entry_index"] == 0
        records.delete_document(conn, out["document_id"])
    conn.close()


def test_agent_kit_download() -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    import io
    import zipfile
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    c = TestClient(appmod.app)
    r = c.get("/api/presets/masem-direct/kit")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/zip")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = {n.split("/", 1)[1] for n in z.namelist()}
    assert {"README.md", "preset.json", "output_schema.json", "prompt.md"} <= names and "chain_template.json" not in names
    readme = z.read("masem-direct-kit/README.md").decode()
    assert preset_spec.schema_id(presets.load_all()["masem-direct"]) in readme and "/api/ingest-pdf" in readme
    assert "# EVIDENCE RULES" in z.read("masem-direct-kit/prompt.md").decode()
    r = c.get("/api/presets/claim-graph-adjudication/kit")
    names = {n.split("/", 1)[1] for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist()}
    assert "chain_template.json" in names and "prompt.md" not in names
    assert c.get("/api/presets/no-such-preset/kit").status_code == 404
