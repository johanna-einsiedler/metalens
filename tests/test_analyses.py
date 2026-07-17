"""Analysis/dashboard builder: tidy-rows authz, LLM figure proposal (mocked) +
validate/repair, dashboard-view round-trip, and owner-gating. Skips without Postgres.
"""
from __future__ import annotations

import os
import sys
import warnings

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

warnings.filterwarnings("ignore")
import fixtures  # noqa: E402


def _db_ok() -> bool:
    try:
        from paperlens import records
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def run() -> int:
    if not _db_ok():
        print("  SKIP  no Postgres available")
        return 0
    from paperlens import records, providers, figures_spec
    from paperlens.ingest import ingest
    from fastapi.testclient import TestClient
    from paperlens.app import app

    conn = records.connect(); records.init_db(conn)
    failures = 0

    def check(label, cond, extra=""):
        nonlocal failures
        print(("  PASS  " if cond else "  FAIL  ") + label + (f"  {extra}" if not cond else ""))
        if not cond:
            failures += 1

    # ── seed: one PUBLIC + one PRIVATE dataset owned by anon session "an-alice" ──
    pub = records.create_dataset(conn, title="an public", session_id="an-alice", visibility="public")
    priv = records.create_dataset(conn, title="an private", session_id="an-alice", visibility="private")
    docp = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON), schema_id="forestplot@v1",
                           session_id="an-alice", source_job_id="an-p")
    docq = records.persist(conn, ingest(fixtures.FORESTPLOT_JSON.replace("0000123", "0000888")),
                           schema_id="forestplot@v1", session_id="an-alice", source_job_id="an-q")
    records.assign_document_to_dataset(conn, pub["id"], docp)
    records.assign_document_to_dataset(conn, priv["id"], docq)
    conn.close()

    cl = TestClient(app)
    ALICE = {"X-Session-Id": "an-alice"}
    BOB = {"X-Session-Id": "an-bob"}

    # ── 1. tidy-rows authz ──────────────────────────────────────────────────────
    rb = cl.get("/api/datasets/rows", params={"dataset": [pub["id"]]}, headers=BOB).json()["rows"]
    check("public dataset rows visible to a stranger", len(rb) == 2, str(len(rb)))
    ra = cl.get("/api/datasets/rows", params={"dataset": [priv["id"]]}, headers=ALICE).json()["rows"]
    rp = cl.get("/api/datasets/rows", params={"dataset": [priv["id"]]}, headers=BOB).json()["rows"]
    check("owner sees private rows", len(ra) == 2, str(len(ra)))
    check("stranger sees NO private rows", len(rp) == 0, str(len(rp)))
    check("row shape has field_values/year/dataset_id",
          {"field_values", "year", "dataset_id"} <= set(ra[0].keys()), str(sorted(ra[0].keys())))
    both = cl.get("/api/datasets/rows", params={"dataset": [pub["id"], priv["id"]]}, headers=ALICE).json()["rows"]
    check("multi-dataset union (owner sees both)", len(both) == 4, str(len(both)))

    # ── 1b. traceability provenance (document_id + batch endpoint) ───────────────
    check("tidy row carries document_id", ra[0].get("document_id") == str(docq), str(ra[0].get("document_id")))
    a_rid = ra[0]["record_id"]
    rd = cl.get(f"/api/records/{a_rid}", headers=ALICE).json()
    check("record_detail carries document_id", rd["record"].get("document_id") == str(docq), str(rd["record"].get("document_id")))
    pub_ids = [r["record_id"] for r in cl.get("/api/datasets/rows", params={"dataset": [pub["id"]]}, headers=ALICE).json()["rows"]]
    priv_ids = [r["record_id"] for r in ra]
    # owner: provenance for both public + private records; shape has document_id + page/snippet keys
    prov_owner = cl.post("/api/records/provenance", headers=ALICE, json={"ids": pub_ids + priv_ids}).json()["records"]
    check("provenance owner sees all requested", len(prov_owner) == 4, str(len(prov_owner)))
    check("provenance shape has document_id + page + snippet",
          {"document_id", "page", "snippet", "paper_title"} <= set(prov_owner[0].keys()), str(sorted(prov_owner[0].keys())))
    # stranger: only PUBLIC records come back; private ids are dropped
    prov_bob = cl.post("/api/records/provenance", headers=BOB, json={"ids": pub_ids + priv_ids}).json()["records"]
    got_bob = {r["record_id"] for r in prov_bob}
    check("provenance stranger gets only public records",
          got_bob == set(pub_ids) and not (got_bob & set(priv_ids)), str(len(prov_bob)))

    # ── 2. propose-figures (mocked provider) + validate/repair ──────────────────
    # synonyms (bar_chart→bar, scatterplot→scatter) + alias keys (field→var) are
    # NORMALIZED, not dropped; only a non-object figure is dropped.
    canned = ('{"figures":[{"title":"By design","chart_kind":"bar_chart",'
              '"encodings":{"y":{"field":"design"}},"transform":{"aggregate":"count"}},'
              '{"title":"scatter","chart_kind":"scatterplot",'
              '"encodings":{"x":{"var":"value"},"y":{"field":"p_value"}}},'
              '"not-a-figure"]}')
    orig = providers.generate_text
    providers.generate_text = lambda *a, **k: canned
    try:
        r = cl.post("/api/analyses/propose-figures", headers=ALICE, json={
            "entry": "dataset", "dataset_id": pub["id"], "goals": "how it varies",
            "model": "gpt-x", "api_key": "k"})
        body = r.json()
        check("propose-figures 200 + ok", r.status_code == 200 and body.get("ok"), str(r.status_code))
        check("raw model output returned", body.get("raw") == canned, str(body.get("raw"))[:60])
        figs = body.get("figures", [])
        check("synonym/alias figures kept, non-object dropped",
              len(figs) == 2 and len(body.get("dropped", [])) == 1, str(body))
        kinds = {f["chart_kind"] for f in figs}
        check("chart_kind synonyms normalized (bar_chart→bar, scatterplot→scatter)",
              kinds == {"bar", "scatter"}, str(kinds))
        scat = next(f for f in figs if f["chart_kind"] == "scatter")
        check("encoding alias field→var normalized",
              scat["encodings"]["x"]["var"] == "value" and scat["encodings"]["y"]["var"] == "p_value",
              str(scat["encodings"]))
        names = {rv["name"] for f in figs for rv in f["required_variables"]}
        check("figure invariant: encoding vars ∈ required_variables",
              {"design", "value", "p_value"} <= names, str(names))

        providers.generate_text = lambda *a, **k: "sorry, I can't."
        r2 = cl.post("/api/analyses/propose-figures", headers=ALICE, json={
            "entry": "dataset", "dataset_id": pub["id"], "model": "gpt-x", "api_key": "k"})
        check("malformed model output → 200, no figures (never 500)",
              r2.status_code == 200 and r2.json().get("figures") == [], str(r2.status_code))
    finally:
        providers.generate_text = orig

    # non-owner cannot propose over a private dataset
    npd = cl.post("/api/analyses/propose-figures", headers=BOB, json={
        "entry": "dataset", "dataset_id": priv["id"], "model": "gpt-x", "api_key": "k"})
    check("propose over stranger's private dataset → 404", npd.status_code == 404, str(npd.status_code))

    # ── 3. dashboard view round-trip + analysis rows ────────────────────────────
    figs = [{"id": "f1", "title": "By design", "chart_kind": "bar",
             "encodings": {"y": {"var": "design"}}, "transform": {"aggregate": "count"},
             "required_variables": [{"name": "design", "type": "categorical", "optional": False}],
             "data_sufficiency": "ok"}]
    cv = cl.post("/api/views", headers=ALICE, json={
        "title": "design dashboard", "view_type": "dashboard", "dataset_ids": [pub["id"]],
        "visibility": "private", "viz_config": {"goals": "g", "figures": figs, "theme": {"vibe": "journal"}}})
    check("create dashboard view → 200", cv.status_code == 200, str(cv.status_code))
    vid = cv.json()["id"]
    gv = cl.get(f"/api/views/{vid}", headers=ALICE).json()
    check("view_type=dashboard round-trips", gv.get("view_type") == "dashboard", str(gv.get("view_type")))
    check("viz_config.figures + theme intact",
          len(gv["viz_config"]["figures"]) == 1 and gv["viz_config"]["theme"]["vibe"] == "journal", str(gv.get("viz_config")))
    rr = cl.get(f"/api/analyses/{vid}/rows", headers=ALICE).json()["rows"]
    check("analysis rows over its dataset", len(rr) == 2, str(len(rr)))

    # ── 4. owner-gating on a private dashboard ──────────────────────────────────
    check("stranger blocked on private analysis rows → 404",
          cl.get(f"/api/analyses/{vid}/rows", headers=BOB).status_code == 404)

    # ── 5. validator invariant (unit) ───────────────────────────────────────────
    kept, _ = figures_spec.validate_figures({"figures": [
        {"title": "forest", "chart_kind": "forest",
         "encodings": {"x": {"var": "yi"}, "y": {"var": "study"}, "error": {"lo": "lo", "hi": "hi"}},
         "required_variables": []}]})
    ok = kept and {"yi", "study", "lo", "hi"} <= {r["name"] for r in kept[0]["required_variables"]}
    check("validator repairs missing required_variables from encodings", bool(ok), str(kept))

    # pages served
    check("/builder + /analysis served",
          cl.get("/builder").status_code == 200 and cl.get("/analysis").status_code == 200)

    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_analyses() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
