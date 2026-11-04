"""Releases of a curated workflow: cutting and versioning, the pinned presets a release records,
the bundle a release is deposited as, a DOI per release on Zenodo (mock transport) with the
``requires`` relations to the presets' DOIs, the inventory and card API, and the moderator
gating. Skips without Postgres where a database is needed."""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import uuid
import zipfile

import httpx
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_analysis_table import _db_ok, _seed  # noqa: E402
from test_regression_tables_preset import RESULT  # noqa: E402
from test_zenodo import FakeZenodo  # noqa: E402

from paperlens import (  # noqa: E402
    preset_releases,
    records,
    workflow_bundle,
    workflow_cards,
    workflows,
    zenodo,
)
from paperlens import workflow_releases as wr
from paperlens import workflow_spec as ws  # noqa: E402

WID = "econ-regressions"
_REAL_LOAD_ALL = workflows.load_all


def _clean(conn, *wids):
    conn.execute("DELETE FROM workflow_release WHERE workflow_id = ANY(%s)", (list(wids),)); conn.commit()


def _retitled(tagline: str):
    real = workflows.load_all
    def patched():
        allw = real()
        allw[WID] = copy.deepcopy(allw[WID]); allw[WID]["meta"]["tagline"] = tagline
        return allw
    return patched


def _fake_release(spec: dict, **over) -> dict:
    return {"id": str(uuid.uuid4()), "workflow_id": spec["id"], "number": 1, "created_at": "2026-11-04T10:00:00+00:00", "notes": "first cut",
            "contract": ws.workflow_id(spec), "spec": spec, "spec_sha": ws.spec_sha(spec),
            "presets": [{"step": "tables", "preset_id": "econ-regression-tables", "schema_id": spec["steps"][0]["preset"], "preset_release": 2, "concept_doi": "10.5072/zenodo.77"}],
            "engine": {"name": "metalens", "version": "2.0.0", "git_sha": "abcdef0123"}, "doi": None, **over}


# ── no database: the bundle and the citation ──────────────────────────────────

def test_the_bundle_is_a_loadable_workflow_folder_with_its_papers(tmp_path) -> None:
    spec = workflows.load_all()[WID]
    rel = _fake_release(spec)
    files = workflow_bundle.build(spec, rel, doi="10.5072/zenodo.9", concept_doi="10.5072/zenodo.8", card_url="https://metalens.tech/workflow?id=econ-regressions")
    assert set(files) == {"workflow.json", "card.md", "steps.md", "CITATION.cff", "README.md"}
    doc = json.loads(files["workflow.json"])
    assert doc["card"] == {"file": "card.md"} and "implicit" not in doc
    # the folder loads back as the same contract
    for name, body in files.items():
        (tmp_path / name).write_bytes(body)
    loaded = ws.load_dir(tmp_path)
    assert ws.workflow_id(loaded[WID]) == ws.workflow_id(spec) and ws.has_card(loaded[WID])
    steps = files["steps.md"].decode()
    for st in spec["steps"]:
        assert f"`{st['id']}`" in steps
    assert "preset release v2" in steps and "10.5072/zenodo.77" in steps and "random sample of 30 records" in steps
    cff = files["CITATION.cff"].decode()
    assert 'doi: "10.5072/zenodo.9"' in cff and 'value: "10.5072/zenodo.8"' in cff and 'value: "10.5072/zenodo.77"' in cff and "type: software" in cff
    readme = files["README.md"].decode()
    assert "workflow release v1" in readme and "10.5072/zenodo.9" in readme and "(version 1)" in readme and "preset release v2" in readme
    blob = workflow_bundle.as_zip(files, WID, 1)
    assert set(os.path.basename(n) for n in zipfile.ZipFile(io.BytesIO(blob)).namelist()) == set(files)


def test_citations_name_the_version_and_the_concept_doi(monkeypatch) -> None:
    monkeypatch.setenv("PAPERLENS_ZENODO_SANDBOX", "1")       # a 10.5072 DOI counts only while the sandbox is on
    spec = workflows.load_all()[WID]
    rel = _fake_release(spec)
    url = "https://metalens.tech/workflow?id=econ-regressions"
    without = wr.citation(spec, None, concept_doi=None, url=url)
    assert "Metalens workflow [Software]" in without and url in without and "(version" not in without
    with_rel = wr.citation(spec, rel, concept_doi="10.5072/zenodo.8", url=url)
    assert "(2026). " in with_rel and "Metalens workflow (version 1) [Software]. Metalens. https://doi.org/10.5072/zenodo.8" in with_rel
    assert wr.citation_for_version(spec, {**rel, "doi": "10.5072/zenodo.9"}, url=url).endswith("https://doi.org/10.5072/zenodo.9")
    assert wr.changed_since(spec, None) and not wr.changed_since(spec, rel)


def test_zenodo_metadata_relates_the_presets_and_the_datasets() -> None:
    spec = workflows.load_all()[WID]
    rel = _fake_release(spec)
    m = zenodo.workflow_metadata(spec, rel, card_url="https://metalens.tech/workflow?id=econ-regressions",
                                 preset_dois=["10.5072/zenodo.77"], dataset_dois=["10.5072/zenodo.300"])["metadata"]
    assert m["upload_type"] == "software" and m["version"] == "v1" and m["title"].endswith("— Metalens workflow v1")
    assert {"Metalens", "workflow"} <= set(m["keywords"]) and m["creators"][0]["name"].startswith("Einsiedler")
    rel_ids = {(r["identifier"], r["relation"]) for r in m["related_identifiers"]}
    assert ("10.5072/zenodo.77", "requires") in rel_ids and ("10.5072/zenodo.300", "isSourceOf") in rel_ids
    assert ("https://metalens.tech/workflow?id=econ-regressions", "isDocumentedBy") in rel_ids
    assert ("https://github.com/johanna-einsiedler/metalens", "isPartOf") in rel_ids
    assert "first cut" in m["description"] and "3 steps" in m["description"] or "4 steps" in m["description"]


# ── with Postgres: releases, DOIs, cards ──────────────────────────────────────

def test_workflow_releases_are_cut_and_versioned(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    _clean(conn, WID)
    assert wr.latest(conn, WID) is None
    r1 = wr.create(conn, WID, notes="first cut")
    spec = workflows.load_all()[WID]
    assert r1["number"] == 1 and r1["contract"] == ws.workflow_id(spec) and r1["spec_sha"] == ws.spec_sha(spec) and r1["notes"] == "first cut"
    assert r1["spec"]["card"]["text"].startswith("## ") and r1["engine"]["name"] == "metalens"
    pins = {p["step"]: p for p in r1["presets"]}
    assert pins["tables"]["schema_id"] == spec["steps"][0]["preset"] and pins["tables"]["preset_id"] == "econ-regression-tables"
    assert records.get_schema(conn, pins["tables"]["schema_id"]) is not None       # the pinned contract exists as a row
    with pytest.raises(ValueError, match="Nothing changed since v1"):
        wr.create(conn, WID)
    # wording moves, the contract does not: v2 under the same contract
    monkeypatch.setattr(workflows, "load_all", _retitled("Changed wording."))
    r2 = wr.create(conn, WID)
    monkeypatch.setattr(workflows, "load_all", _REAL_LOAD_ALL)
    assert r2["number"] == 2 and r2["contract"] == r1["contract"] and r2["spec_sha"] != r1["spec_sha"]
    assert [r["number"] for r in wr.list_for_workflow(conn, WID)] == [2, 1] and [r["number"] for r in wr.by_contract(conn, r1["contract"])] == [1, 2]
    assert wr.get_by_number(conn, WID, 1)["id"] == r1["id"] and wr.latest(conn, f"{WID}@{r1['contract'][-8:]}")["number"] == 2
    assert wr.changed_since(spec, wr.latest(conn, WID)) is True                   # live wording differs from v2's
    # a run started now records the release whose document it matches exactly
    assert workflows._release_number(conn, spec) == 1   # noqa: SLF001
    # a workflow without a card cannot be released
    (tmp_path / "plain.json").write_text(json.dumps({"format": 1, "id": "plain-test", "meta": {"title": "Plain"},
                                                     "steps": [{"id": "x", "kind": "extract", "preset": spec["steps"][0]["preset"], "source": "supplied"}]}))
    monkeypatch.setenv("PAPERLENS_WORKFLOW_DIRS", str(tmp_path))
    assert "plain-test" in workflows.load_all()
    with pytest.raises(LookupError, match="not a curated workflow"):
        wr.create(conn, "plain-test")
    monkeypatch.delenv("PAPERLENS_WORKFLOW_DIRS")
    _clean(conn, WID); conn.close()


def test_a_doi_per_workflow_release(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    monkeypatch.setenv("PAPERLENS_ZENODO_TOKEN", "t-test"); monkeypatch.setenv("PAPERLENS_ZENODO_SANDBOX", "1")
    conn = records.connect(); records.init_db(conn)
    _clean(conn, WID)
    conn.execute("DELETE FROM preset_release WHERE preset_id = 'econ-regression-tables'"); conn.commit()
    z = FakeZenodo(); client = httpx.Client(transport=httpx.MockTransport(z.handler))
    # the pinned preset has a released, deposited version: the workflow release records and requires it
    p1 = preset_releases.create(conn, "econ-regression-tables", notes="preset cut")
    zenodo.deposit_preset(conn, p1, client=client)
    assert preset_releases.concept_doi(conn, "econ-regression-tables") == "10.5072/zenodo.101"
    r1 = wr.create(conn, WID, notes="first cut")
    pin = r1["presets"][0]
    assert pin["preset_release"] == 1 and pin["concept_doi"] == "10.5072/zenodo.101"
    url = workflow_cards.card_url(WID)
    out = zenodo.deposit_workflow(conn, r1, client=client, card_url=url, dataset_dois=["10.5072/zenodo.900"])
    assert out["doi"] == "10.5072/zenodo.102" and out["zenodo_record_id"] == 102 and out["zenodo_concept_doi"] == "10.5072/zenodo.102"
    assert wr.concept_doi(conn, WID) == "10.5072/zenodo.102"
    files = z.buckets["b102"]
    assert set(files) == {"workflow.json", "card.md", "steps.md", "CITATION.cff", "README.md"}
    assert 'doi: "10.5072/zenodo.102"' in files["CITATION.cff"].decode() and 'value: "10.5072/zenodo.101"' in files["CITATION.cff"].decode()
    assert json.loads(files["workflow.json"])["card"] == {"file": "card.md"} and "preset release v1" in files["steps.md"].decode()
    m = z.deps[102]["metadata"]
    assert m["upload_type"] == "software" and m["version"] == "v1" and m["license"] == "cc-by-4.0"
    rel_ids = {(r["identifier"], r["relation"]) for r in m["related_identifiers"]}
    assert ("10.5072/zenodo.101", "requires") in rel_ids and ("10.5072/zenodo.900", "isSourceOf") in rel_ids and (url, "isDocumentedBy") in rel_ids
    with pytest.raises(RuntimeError, match="already has a DOI"):
        zenodo.deposit_workflow(conn, wr.get(conn, r1["id"]), client=client)
    card = workflow_cards.card(conn, WID)
    assert card["concept_doi"] == "10.5072/zenodo.102" and "https://doi.org/10.5072/zenodo.102" in card["cite_as"]
    assert card["releases"][0]["doi"] == "10.5072/zenodo.102" and card["releases"][0]["presets"][0]["preset_release"] == 1
    # a second version: a new version of the same record
    monkeypatch.setattr(workflows, "load_all", _retitled("Changed wording."))
    r2 = wr.create(conn, WID)
    monkeypatch.setattr(workflows, "load_all", _REAL_LOAD_ALL)
    out2 = zenodo.deposit_workflow(conn, r2, client=client, card_url=url)
    assert out2["doi"] == "10.5072/zenodo.103" and z.deps[103]["conceptrecid"] == 102
    assert "POST /api/deposit/depositions/102/actions/newversion" in z.log
    # the daily cap counts workflow deposits made by the account
    uid = str(uuid.uuid4())
    conn.execute("INSERT INTO users (id, email, password_hash) VALUES (%s::uuid, %s, 'x') ON CONFLICT DO NOTHING", (uid, f"cap-{uid[:8]}@example.org"))
    conn.execute("UPDATE workflow_release SET doi_minted_by = %s::uuid WHERE id = %s::uuid", (uid, r2["id"])); conn.commit()
    monkeypatch.setenv("PAPERLENS_ZENODO_PER_DAY", "1")
    assert "limit is 1" in (zenodo.may_mint(conn, {"id": uid, "email": f"cap-{uid[:8]}@example.org"}) or "")
    monkeypatch.delenv("PAPERLENS_ZENODO_PER_DAY")
    # sandbox → real Zenodo: the test DOIs stop counting, the lineage starts fresh
    monkeypatch.delenv("PAPERLENS_ZENODO_SANDBOX")
    card = workflow_cards.card(conn, WID)
    assert card["concept_doi"] is None and card["releases"][0]["doi"] is None and "10.5072" not in card["cite_as"]
    real = FakeZenodo(); real.n = 500
    out3 = zenodo.deposit_workflow(conn, wr.get(conn, r2["id"]), client=httpx.Client(transport=httpx.MockTransport(real.handler)), card_url=url)
    assert out3["doi"] == "10.5072/zenodo.501" and real.log[0] == "POST /api/deposit/depositions"
    conn.execute("DELETE FROM users WHERE id = %s::uuid", (uid,)); conn.commit()
    _clean(conn, WID); conn.execute("DELETE FROM preset_release WHERE preset_id = 'econ-regression-tables'"); conn.commit(); conn.close()


def test_api_workflow_cards_releases_and_bundle(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    from fastapi.testclient import TestClient

    from paperlens import app as appmod
    monkeypatch.delenv("PAPERLENS_BRAND", raising=False); monkeypatch.delenv("PAPERLENS_BASIC_PASSWORD", raising=False)
    monkeypatch.delenv("PAPERLENS_ZENODO_TOKEN", raising=False); monkeypatch.delenv("PAPERLENS_LOCAL_MODE", raising=False)
    conn = records.connect(); records.init_db(conn)
    _clean(conn, WID)
    # a public dataset declaring the workflow shows under "used by"
    sess = f"wc-{uuid.uuid4().hex[:6]}"
    ds, _doc = _seed(conn, RESULT, "econ-regression-tables", sess)
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    c = TestClient(appmod.app)
    r = c.patch(f"/api/datasets/{ds}", headers={"X-Session-Id": sess}, json={"workflow": WID}); assert r.status_code == 200, r.text
    lst = c.get("/api/workflows/cards").json()
    ids = {x["id"] for x in lst["cards"]}
    assert {WID, "econ-claims", "masem"} <= ids and "zenodo" not in lst
    econ = next(x for x in lst["cards"] if x["id"] == WID)
    assert econ["latest"] is None and econ["contract"].startswith(f"{WID}@") and econ["counts"]["datasets"] >= 1
    assert [s["kind"] for s in econ["steps"]][0] == "extract" and econ["review"][0]["mode"] == "sample"
    assert c.get("/api/workflows/no-such-workflow/card").status_code == 404
    card = c.get(f"/api/workflows/{WID}/card").json()
    assert card["id"] == WID and card["card_md"].startswith("## Introduction") and card["viewer"]["is_admin"] is False and "zenodo" not in card
    by = {s["id"]: s for s in card["steps"]}
    assert by["tables"]["preset_card"]["id"] == "econ-regression-tables" and by["tables"]["preset_card"]["url"] == "/preset?id=econ-regression-tables"
    assert by["tables"]["review"]["mode"] == "sample" and by["tables"]["review"]["n"] == 30
    assert "se_dangling_ref" in by["numbers"]["check_info"]["codes"] and by["contract"]["check"] == "schema"
    assert card["releases"] == [] and "(version" not in card["cite_as"] and card["verification"]["n_datasets"] >= 1
    assert any(d["id"] == ds for d in card["datasets"]) and "`tables`" in card["steps_md"]
    assert c.get("/workflows").status_code == 200 and "wf-tiles" in c.get("/workflows").text
    assert c.get(f"/workflow?id={WID}").status_code == 200 and "wc-body" in c.get(f"/workflow?id={WID}").text
    # releases are a moderator's act
    mine = {"X-Session-Id": sess}
    assert c.post(f"/api/workflows/{WID}/releases", json={"notes": "x"}, headers=mine).status_code == 403
    email = f"wc-{uuid.uuid4().hex[:8]}@example.org"
    c.post("/api/auth/register", json={"email": email, "password": "cards-test-pass-1"}, headers=mine)
    assert c.post(f"/api/workflows/{WID}/releases", json={"notes": "x"}, headers=mine).status_code == 403
    monkeypatch.setenv("PAPERLENS_ADMINS", f" {email.upper()} ")
    made = c.post(f"/api/workflows/{WID}/releases", json={"notes": "first"}, headers=mine).json()
    assert made["number"] == 1 and made["problems"] == [] and made["doi"] is None and made["presets"][0]["step"] == "tables"
    assert c.post(f"/api/workflows/{WID}/releases", json={}, headers=mine).status_code == 409
    assert c.post("/api/workflows/no-such-workflow/releases", json={}, headers=mine).status_code == 404
    assert c.post(f"/api/workflows/{WID}/releases/1/doi", headers=mine).status_code == 400       # Zenodo not configured
    assert c.post(f"/api/workflows/{WID}/releases/9/doi", headers=mine).status_code == 404
    card = c.get(f"/api/workflows/{WID}/card", headers=mine).json()
    assert card["viewer"]["is_admin"] is True and card["zenodo"]["configured"] is False and card["latest"]["number"] == 1
    assert card["changed_since_release"] is False and "(version 1)" in card["cite_as"]
    assert c.get("/api/workflows/cards", headers=mine).json()["viewer"]["is_admin"] is True
    # the bundle downloads as a zip of the loadable folder
    z = c.get(f"/api/workflows/{WID}/releases/1/bundle")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = {os.path.basename(n) for n in zipfile.ZipFile(io.BytesIO(z.content)).namelist()}
    assert names == {"workflow.json", "card.md", "steps.md", "CITATION.cff", "README.md"}
    assert c.get(f"/api/workflows/{WID}/releases/2/bundle").status_code == 404
    # a run started now records the release it matches
    conn.rollback()
    assert workflows._release_number(conn, workflows.load_all()[WID]) == 1   # noqa: SLF001
    _clean(conn, WID); conn.close()
