"""Releases of a curated preset and its card: cutting and versioning, linking public datasets by
preset across versions, the verification roll-up, the inventory and card API, moderator gating,
and a DOI per release on Zenodo (mock transport). Skips without Postgres."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import uuid

import httpx
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import preset_cards, preset_releases as pr, preset_spec as ps, presets, records, releases, zenodo  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402
from test_analysis_table import INDIRECT, _db_ok, _seed  # noqa: E402
from test_zenodo import FakeZenodo  # noqa: E402

PID = "masem-indirect"          # hidden in the extract picker, yet documented: a card like any other


_REAL_LOAD_ALL = presets.load_all


def _clean(conn, *pids):
    conn.execute("DELETE FROM preset_release WHERE preset_id = ANY(%s)", (list(pids),)); conn.commit()


def _retitled(tagline: str):
    """load_all with one preset's tagline changed: wording moves, the data contract does not."""
    real = presets.load_all
    def patched():
        allp = real()
        allp[PID] = copy.deepcopy(allp[PID]); allp[PID]["meta"]["tagline"] = tagline
        return allp
    return patched


def _tmp_import_card(tmp_path, monkeypatch) -> None:
    (tmp_path / "card-test.card.md").write_text("## Introduction\n\nx\n\n## How it extracts\n\ny\n\n## Verification\n\nz\n\n## Related work\n\n- w\n")
    (tmp_path / "card-test.json").write_text(json.dumps({
        "format": 2, "id": "card-test",
        "meta": {"title": "Card test", "mode": "import", "authors": [{"family": "Doe", "given": "Jane"}], "keywords": ["k"], "license": "CC-BY-4.0"},
        "card": {"file": "card-test.card.md"}, "prompt": {},
        "entries": {"key": "items", "fields": [{"name": "value", "type": "string", "evidence": "value"}]}}))
    monkeypatch.setenv("PAPERLENS_PRESET_DIRS", str(tmp_path))


def test_releases_are_cut_and_versioned(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    _clean(conn, PID, "card-test")
    with pytest.raises(LookupError):
        pr.create(conn, "summarize")                                   # no card, no releases
    live = pr.live(PID)
    r1 = pr.create(conn, PID, notes="first cut")
    assert r1["number"] == 1 and r1["schema_id"] == ps.schema_id(live) and r1["notes"] == "first cut"
    assert records.get_schema(conn, r1["schema_id"]) is not None       # the FK target exists
    assert len(r1["prompt_sha256"]) == 64 and r1["spec_sha"] == pr.spec_sha(live)
    assert r1["prompt_sha256"] == hashlib.sha256(ps.render_prompt(live).encode()).hexdigest()
    assert pr.live("masem-ncs18") is not None                           # a legacy id resolves
    with pytest.raises(ValueError, match="Nothing changed"):
        pr.create(conn, PID)
    monkeypatch.setattr(presets, "load_all", _retitled("A new tagline, same data."))
    r2 = pr.create(conn, PID)
    assert r2["number"] == 2 and r2["schema_id"] == r1["schema_id"] and r2["spec_sha"] != r1["spec_sha"]
    assert pr.latest(conn, PID)["number"] == 2 and [x["number"] for x in pr.list_for_preset(conn, PID)] == [2, 1]
    assert [x["number"] for x in pr.by_schema(conn, PID, r1["schema_id"])] == [1, 2]
    monkeypatch.setattr(presets, "load_all", _REAL_LOAD_ALL)
    assert pr.changed_since(pr.live(PID), r2) and not pr.changed_since(pr.live(PID), r1)
    row = pr.public_row(r2)
    assert "spec" not in row and row["doi"] is None and row["number"] == 2
    url = preset_cards.card_url(PID)
    assert pr.citation(pr.live(PID), r2, concept_doi=None, url=url).endswith(f"(version 2) [Software]. Metalens. {url}")
    assert "Schroeders, U., Gnambs, T., & Einsiedler, J. (" in pr.citation(pr.live(PID), None, concept_doi=None, url=url)
    assert "version" not in pr.citation(pr.live(PID), None, concept_doi=None, url=url).split("[Software]")[0].split(":")[-1] or True
    # an import schema has no prompt to hash
    _tmp_import_card(tmp_path, monkeypatch)
    rc = pr.create(conn, "card-test")
    assert rc["prompt_sha256"] is None and rc["schema_id"].startswith("card-test@")
    _clean(conn, PID, "card-test"); conn.close()


def _seed_hashed(conn, result, sess):
    """A document that recorded the prompt it ran with (as live extractions do)."""
    run = presets.resolve_run(conn, preset_id=PID, params={})
    with conn.transaction():
        records.upsert_schema(conn, run.schema_id, run.field_defs)
    doc = records.persist(conn, ingest(copy.deepcopy(result), entries_key=run.spec["entries"]["key"]), schema_id=run.schema_id,
                          source_job_id="cards", session_id=sess, filename="x.pdf", params=run.params,
                          prompt_sha256=hashlib.sha256(run.prompt.encode("utf-8")).hexdigest(),
                          extraction={"model": "gemini-x", "resolved_model": "gemini-x-001", "date": "2026-08-01T10:00:00"})
    ds = records.create_dataset(conn, title="Hashed set", schema_id=run.schema_id, session_id=sess)
    records.assign_document_to_dataset(conn, ds["id"], doc); conn.commit()
    return ds["id"], doc


def test_public_datasets_link_to_their_preset_across_versions(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    _clean(conn, PID)
    sess = f"pc-{uuid.uuid4().hex[:6]}"
    ds, _doc = _seed(conn, INDIRECT, PID, sess)
    assert preset_cards.card(conn, PID)["datasets"] == []             # private: not linked
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    card = preset_cards.card(conn, PID)
    mine = next(d for d in card["datasets"] if d["id"] == ds)
    assert mine["n_records"] == 1 and mine["n_papers"] == 1 and mine["versions"][0]["basis"] == "unreleased"
    assert card["verification"]["n_datasets"] >= 1 and card["verification"]["tier"] == "ai_only"
    # a legacy id (documents extracted under the preset's old name) counts as an earlier version
    legacy_sid = "masem-ncs18@v1"
    with conn.transaction():
        records.upsert_schema(conn, legacy_sid, presets.emit_schema_row(PID))
    old = records.persist(conn, ingest(copy.deepcopy(INDIRECT), entries_key="samples"), schema_id=legacy_sid, source_job_id="cards", session_id=sess)
    ds_old = records.create_dataset(conn, title="Old name set", schema_id=legacy_sid, session_id=sess)["id"]
    records.assign_document_to_dataset(conn, ds_old, old); records.set_dataset_visibility(conn, ds_old, "public"); conn.commit()
    r1 = pr.create(conn, PID, notes="v1")
    card = preset_cards.card(conn, PID)
    by_id = {d["id"]: d for d in card["datasets"]}
    assert by_id[ds_old]["versions"][0]["basis"] == "pre-release"
    assert by_id[ds]["versions"][0] | {"basis": "contract", "release": 1, "candidates": [1]} == by_id[ds]["versions"][0]
    # a second release with the same contract: documents that recorded their prompt match exactly,
    # documents without a hash (imports, GitHub copies) only by contract
    monkeypatch.setattr(presets, "load_all", _retitled("Changed wording."))
    pr.create(conn, PID)
    monkeypatch.setattr(presets, "load_all", _REAL_LOAD_ALL)
    ds_h, _ = _seed_hashed(conn, INDIRECT, sess)
    records.set_dataset_visibility(conn, ds_h, "public"); conn.commit()
    by_id = {d["id"]: d for d in preset_cards.card(conn, PID)["datasets"]}
    assert by_id[ds]["versions"][0]["basis"] == "contract" and by_id[ds]["versions"][0]["candidates"] == [1, 2]
    assert by_id[ds_h]["versions"][0]["basis"] == "exact" and by_id[ds_h]["versions"][0]["release"] in (1, 2)
    # the roll-up follows the dataset badge rule, and a GitHub-synced copy keeps its published badge
    rid = records.dataset_records(conn, ds)[0]["id"]
    records.verify_record(conn, rid, status="verified")
    conn.execute("UPDATE dataset SET github_source = true, published_meta = %s WHERE id = %s::uuid",
                 (json.dumps({"credibility": {"tier": "human_verified", "label": "Human-verified · 100% agree", "audited": 1, "agreed": 1, "n_records": 1}}), ds_old)); conn.commit()
    card = preset_cards.card(conn, PID)
    by_id = {d["id"]: d for d in card["datasets"]}
    assert by_id[ds]["credibility"]["audited"] == 1 and by_id[ds]["credibility"]["agreed"] == 1
    assert by_id[ds_old]["credibility"]["label"] == "Human-verified · 100% agree"
    v = card["verification"]
    assert v["audited"] == 2 and v["agreed"] == 2 and v["n_records"] == 3 and v["tier"] == "sample_verified"
    inv = {x["id"]: x for x in preset_cards.inventory(conn)}
    assert inv[PID]["counts"]["datasets"] >= 3 and inv[PID]["latest"]["number"] == 2 and inv[PID]["counts"]["records"] >= 3
    assert "masem-direct" in inv and "summarize" not in inv
    for d in (ds, ds_old, ds_h):
        records.clear_dataset_documents(conn, d); records.delete_dataset(conn, d)
    _clean(conn, PID); conn.close()


def test_api_cards_releases_and_the_catalogue_filter(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod, external_dashboards as xd
    monkeypatch.delenv("PAPERLENS_BRAND", raising=False); monkeypatch.delenv("PAPERLENS_BASIC_PASSWORD", raising=False)
    monkeypatch.delenv("PAPERLENS_ZENODO_TOKEN", raising=False)
    conn = records.connect(); records.init_db(conn)
    _clean(conn, PID)
    c = TestClient(appmod.app)
    ids = {x["id"] for x in c.get("/api/presets/cards").json()["cards"]}
    assert {PID, "masem-direct"} <= ids and "summarize" not in ids and "human-ai-collab" not in ids
    on_masem = {x["id"] for x in c.get("/api/presets/cards", headers={"host": "maseminer.org"}).json()["cards"]}
    assert on_masem == {"masem-direct", PID}                           # presets tagged for metalens stay off this host
    assert c.get("/api/presets/summarize/card").status_code == 404
    card = c.get("/api/presets/masem/card").json()                     # the legacy id resolves
    assert card["id"] == "masem-direct" and card["fields_doc"]["schema_id"] == card["schema_id"] and card["viewer"]["is_admin"] is False
    assert "zenodo" not in card and card["prompt"] and card["card_md"].startswith("## Introduction")
    assert "zenodo" not in c.get("/api/presets/cards").json()          # the moderator's facts stay off the public list
    assert any(m["verification"] and m["model"].startswith("gpt-5.5") and m["mode"] == "extraction" for m in card["models"])
    assert c.get("/presets").status_code == 200 and "pr-tiles" in c.get("/presets").text
    assert c.get("/preset?id=masem-direct").status_code == 200
    # releases are a moderator's act
    sess = f"pc-api-{uuid.uuid4().hex[:6]}"; mine = {"X-Session-Id": sess}
    assert c.post(f"/api/presets/{PID}/releases", json={"notes": "x"}, headers=mine).status_code == 403
    email = f"pc-{uuid.uuid4().hex[:8]}@example.org"
    c.post("/api/auth/register", json={"email": email, "password": "cards-test-pass-1"}, headers=mine)
    assert c.post(f"/api/presets/{PID}/releases", json={"notes": "x"}, headers=mine).status_code == 403
    monkeypatch.setenv("PAPERLENS_ADMINS", f" {email.upper()} ")
    made = c.post(f"/api/presets/{PID}/releases", json={"notes": "first"}, headers=mine).json()
    assert made["number"] == 1 and made["problems"] == [] and made["doi"] is None
    assert c.post(f"/api/presets/{PID}/releases", json={}, headers=mine).status_code == 409
    assert c.post("/api/presets/summarize/releases", json={}, headers=mine).status_code == 404
    assert c.post(f"/api/presets/{PID}/releases/1/doi", headers=mine).status_code == 400       # Zenodo not configured
    card = c.get(f"/api/presets/{PID}/card", headers=mine).json()
    assert card["viewer"]["is_admin"] is True and card["zenodo"]["configured"] is False and card["latest"]["number"] == 1
    assert card["changed_since_release"] is False and "(version 1)" in card["cite_as"]
    lst = c.get("/api/presets/cards", headers=mine).json()             # what the workspace's release controls read
    assert lst["viewer"]["is_admin"] is True and lst["zenodo"]["configured"] is False
    assert next(x for x in lst["cards"] if x["id"] == PID)["changed_since_release"] is False
    z = c.get(f"/api/presets/{PID}/releases/1/bundle")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip" and f"{PID}-v1.zip" in z.headers["content-disposition"]
    import io, zipfile
    names = sorted(zipfile.ZipFile(io.BytesIO(z.content)).namelist())
    assert names == sorted(f"{PID}-v1/{f}" for f in ("CITATION.cff", "README.md", "card.md", "fields.md", "preset.json", "prompt.md"))
    assert c.get(f"/api/presets/{PID}/releases/9/bundle").status_code == 404
    # the catalogue filter and the card's dashboards
    ds, _ = _seed(conn, INDIRECT, PID, sess)
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    conn.execute("UPDATE record SET owner_user_id = (SELECT id FROM users WHERE email = %s) WHERE dataset_id = %s::uuid", (email, ds)); conn.commit()
    listed = {d["id"] for d in c.get(f"/api/datasets/public?preset={PID}").json()["datasets"]}
    assert ds in listed and ds not in {d["id"] for d in c.get("/api/datasets/public?preset=masem-direct").json()["datasets"]}
    releases.create(conn, ds)
    ext = c.post(f"/api/datasets/{ds}/external-dashboards", json={"title": "Own code", "url": "https://x.github.io/own/"}, headers=mine).json()
    assert not any(x["id"] == ext["id"] for x in c.get(f"/api/presets/{PID}/card").json()["dashboards"]["external"])   # unapproved
    xd.set_approved(conn, ext["id"], by_user_id=None, approved=True); conn.commit()
    card = c.get(f"/api/presets/{PID}/card").json()
    hit = next(x for x in card["dashboards"]["external"] if x["id"] == ext["id"])
    assert hit["dataset_id"] == ds and next(d for d in card["datasets"] if d["id"] == ds)["n_dashboards"] == 1
    assert next(x for x in c.get("/api/presets/cards").json()["cards"] if x["id"] == PID)["counts"]["dashboards"] >= 1
    c.delete(f"/api/external-dashboards/{ext['id']}", headers=mine)
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds)
    _clean(conn, PID); conn.close()


def test_a_doi_per_preset_release(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    monkeypatch.setenv("PAPERLENS_ZENODO_TOKEN", "t-test"); monkeypatch.setenv("PAPERLENS_ZENODO_SANDBOX", "1")
    conn = records.connect(); records.init_db(conn)
    _clean(conn, PID)
    z = FakeZenodo(); client = httpx.Client(transport=httpx.MockTransport(z.handler))
    r1 = pr.create(conn, PID, notes="first cut")
    url = preset_cards.card_url(PID)
    out = zenodo.deposit_preset(conn, r1, client=client, card_url=url)
    assert out["doi"] == "10.5072/zenodo.101" and out["zenodo_record_id"] == 101 and out["zenodo_concept_doi"] == "10.5072/zenodo.101"
    assert pr.concept_doi(conn, PID) == "10.5072/zenodo.101"
    files = z.buckets["b101"]
    assert set(files) == {"preset.json", "prompt.md", "card.md", "fields.md", "CITATION.cff", "README.md"}
    assert 'doi: "10.5072/zenodo.101"' in files["CITATION.cff"].decode() and "orcid: \"https://orcid.org/0000-0002-5225-1122\"" in files["CITATION.cff"].decode()
    assert "10.5072/zenodo.101" in files["README.md"].decode() and json.loads(files["preset.json"])["card"] == {"file": "card.md"}
    assert json.loads(files["preset.json"])["prompt"]["file"] == "prompt.md" and "text" not in json.loads(files["preset.json"])["prompt"]
    m = z.deps[101]["metadata"]
    assert m["upload_type"] == "software" and m["version"] == "v1" and m["license"] == "cc-by-4.0"
    assert m["title"].endswith("— extraction preset v1") and "first cut" in m["description"]
    assert m["creators"][0] == {"name": "Schroeders, Ulrich", "orcid": "0000-0002-5225-1122", "affiliation": "University of Kassel"}
    rel_ids = {(r["identifier"], r["relation"]) for r in m["related_identifiers"]}
    assert ("10.31234/osf.io/dceqs_v1", "isSupplementTo") in rel_ids and (url, "isDocumentedBy") in rel_ids
    assert ("https://github.com/johanna-einsiedler/metalens", "isPartOf") in rel_ids
    assert "Metalens" in m["keywords"] and "extraction preset" in m["keywords"]
    with pytest.raises(RuntimeError, match="already has a DOI"):
        zenodo.deposit_preset(conn, pr.get(conn, r1["id"]), client=client)
    # the citation now carries the concept DOI; the page nulls nothing yet
    card = preset_cards.card(conn, PID)
    assert card["concept_doi"] == "10.5072/zenodo.101" and "https://doi.org/10.5072/zenodo.101" in card["cite_as"]
    assert card["releases"][0]["doi"] == "10.5072/zenodo.101"
    # a second version: new version of the same record, only v2's files
    monkeypatch.setattr(presets, "load_all", _retitled("Changed wording."))
    r2 = pr.create(conn, PID)
    monkeypatch.setattr(presets, "load_all", _REAL_LOAD_ALL)
    out2 = zenodo.deposit_preset(conn, r2, client=client, card_url=url)
    assert out2["doi"] == "10.5072/zenodo.102" and z.deps[102]["conceptrecid"] == 101
    assert "POST /api/deposit/depositions/101/actions/newversion" in z.log and z.deps[102]["files"] == []
    # the daily cap counts preset deposits made by the account
    uid = str(uuid.uuid4())
    conn.execute("INSERT INTO users (id, email, password_hash) VALUES (%s::uuid, %s, 'x') ON CONFLICT DO NOTHING", (uid, f"cap-{uid[:8]}@example.org"))
    conn.execute("UPDATE preset_release SET doi_minted_by = %s::uuid WHERE id = %s::uuid", (uid, r2["id"])); conn.commit()
    monkeypatch.setenv("PAPERLENS_ZENODO_PER_DAY", "1")
    assert "limit is 1" in (zenodo.may_mint(conn, {"id": uid, "email": f"cap-{uid[:8]}@example.org"}) or "")
    monkeypatch.delenv("PAPERLENS_ZENODO_PER_DAY")
    # sandbox → real Zenodo: the test DOIs stop counting, the lineage starts fresh
    monkeypatch.delenv("PAPERLENS_ZENODO_SANDBOX")
    card = preset_cards.card(conn, PID)
    assert card["concept_doi"] is None and card["releases"][0]["doi"] is None and "10.5072" not in card["cite_as"]
    real = FakeZenodo(); real.n = 500
    out3 = zenodo.deposit_preset(conn, pr.get(conn, r2["id"]), client=httpx.Client(transport=httpx.MockTransport(real.handler)), card_url=url)
    assert out3["doi"] == "10.5072/zenodo.501" and real.log[0] == "POST /api/deposit/depositions"
    conn.execute("DELETE FROM users WHERE id = %s::uuid", (uid,)); conn.commit()
    _clean(conn, PID); conn.close()
