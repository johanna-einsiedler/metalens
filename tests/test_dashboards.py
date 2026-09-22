"""Dashboards: the template registry, the validator (repairs, drops, computed sufficiency,
question coverage), the default dashboard per preset, and the stored-dashboard API (ownership,
visibility that follows the dataset, stale-revision 409, cascade with the dataset, sign-in
claim). Skips the DB parts without Postgres."""
from __future__ import annotations

import copy
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import dashboard_spec as ds_spec, dashboards, records  # noqa: E402
from test_analysis_table import HAC, INDIRECT, _db_ok, _seed  # noqa: E402


def test_registry_is_well_formed() -> None:
    reg = ds_spec.registry()
    ids = [t["id"] for t in reg["templates"]]
    assert len(ids) == len(set(ids)) and {"forest", "scatter", "rows_table", "stat_count"} <= set(ids)
    for t in reg["templates"]:
        assert t["type"] in ("figure", "table", "stat") and t["icon"] and t["description"]
        sids = [s["id"] for s in t["slots"]]
        assert len(sids) == len(set(sids))
        assert all(s["types"] is None or set(s["types"]) <= {"number", "integer", "string", "enum", "boolean"} for s in t["slots"])


def test_the_composer_is_the_edit_mode_of_the_dashboard_page() -> None:
    """/compose links from before keep working: they land in /dashboard?…&edit=1."""
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    c = TestClient(appmod.app)
    r = c.get("/compose?dataset=abc", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/dashboard?dataset=abc&edit=1"
    r = c.get("/compose?dashboard=xyz", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/dashboard?id=xyz&edit=1"
    page = c.get("/dashboard").text
    assert 'id="dash-inspector"' in page and "editor.js" not in page          # the editor module is loaded on demand only


def test_colour_slot_is_capped_at_the_pairwise_safe_palette() -> None:
    """Only the first four series colours are distinguishable in any pairing (theme.css)."""
    for tpl in ds_spec.REGISTRY:
        for slot in tpl["slots"]:
            if slot["id"] == "color":
                assert slot["max_distinct"] == 4, tpl["id"]


def test_labels_context_and_theme_are_kept_clean() -> None:
    """Value labels only for real columns, the planner context as text, a theme only from the
    known looks / fonts and #rrggbb colours (they end up in a style attribute)."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    ds, _ = _seed(conn, HAC, "human-ai-collab", f"dash-{uuid.uuid4().hex[:6]}")
    tables, default = _tables(conn, ds)
    spec, _ = ds_spec.validate({
        "blocks": [{"template": "rows_table", "title": "Rows"},
                   {"template": "stat_count", "title": "Papers", "bindings": {"column": {"column": "_title"}}, "options": {"note": "  distinct   papers ", "bogus": 1}}],
        "column_labels": {"_title": " Paper ", "Exp_Design": "Design", "no_such_column": "x", "Metric": ""},
        "context": "  for clinicians  ",
        "value_labels": {"Exp_Design": {"within": " Within   subjects ", "same": "same", "n": 3}, "no_such_column": {"a": "A"}, "Metric": "nope"},
        "theme": {"vibe": "night", "font": "comic", "colors": {"mark": "#AABBCC", "series": ["#112233", "red; } body { display:none", None, "#445566", "zzz"]}},
    }, tables, default_unit=default)
    assert spec["value_labels"] == {"Exp_Design": {"within": "Within subjects", "n": "3"}}
    assert spec["context"] == "for clinicians"
    assert spec["column_labels"]["_title"] == "Paper" and "no_such_column" not in spec["column_labels"] and "Metric" not in spec["column_labels"]
    assert spec["blocks"][1]["options"] == {"note": "distinct papers"}                # a key number's own caption
    assert all(u["n"] >= 1 for u in tables[default]["units"])                          # every layout says how many rows it has
    assert spec["theme"] == {"vibe": "night", "font": None, "colors": {"mark": "#aabbcc", "series": ["#112233", None, None, "#445566"]}}
    assert ds_spec.clean_theme("x") == {"vibe": None, "font": None, "colors": {}}
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.commit(); conn.close()


def _tables(conn, dataset_id):
    return dashboards.tables_for(conn, dataset_id, owner=True)


def test_validator_repairs_drops_and_computes_sufficiency() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    sess = f"dash-{uuid.uuid4().hex[:6]}"
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    tables, default = _tables(conn, ds)
    assert default == "conditions.measures" and set(tables) == {"entries", "conditions", "conditions.measures"}
    proposal = {"questions": ["Does the team beat the human?", {"id": "q2", "text": "Does it depend on task difficulty?"}],
                "blocks": [
                    {"template": "Forest Plot", "title": "Team vs human", "answers": ["q1"], "main_message": "Teams  do better.",
                     "bindings": {"x": {"column": "g_team_vs_human"}, "lower": "g_team_vs_human_lo", "upper": {"column": "G_TEAM_VS_HUMAN_HI"},
                                  "color": {"column": "no_such_column"}},
                     "sufficiency": {"status": "ok", "reason": "plenty"}},
                    {"template": "scatterplot", "unit": "measures", "bindings": {"x": {"column": "Mean human"}, "y": {"column": "Avg_Perf_HumanAI"}},
                     "transform": {"filter": [{"column": "Perf_Dir", "op": "eq", "value": "Up"}, {"column": "ghost", "op": "eq", "value": 1}]}},
                    {"template": "scatter", "bindings": {"x": {"column": "Perf_Metric"}, "y": {"column": "Avg_Perf_AI"}}},
                    {"template": "pie"}, "not a block", {"type": "summary", "template": "text"}],
                "unanswered": [{"question": "q2", "reason": "no column records task difficulty"}]}
    clean, report = ds_spec.validate(proposal, tables, default_unit=default, origin="llm")
    b = {x["template"] + str(i): x for i, x in enumerate(clean["blocks"])}
    forest = clean["blocks"][0]
    assert forest["template"] == "forest" and forest["type"] == "figure" and forest["unit"] == "conditions.measures"
    assert forest["bindings"]["upper"] == {"column": "g_team_vs_human_hi"} and "color" not in forest["bindings"]
    assert forest["bindings"]["label"] == {"columns": ["_study"]}                      # the slot default
    assert forest["sufficiency"] == {"status": "ok", "n_rows": 2, "reason": ""} or forest["sufficiency"]["status"] in ("ok", "partial")
    assert forest["sufficiency"]["n_rows"] == 2 and forest["answers"] == ["q1"] and forest["main_message"] == "Teams do better."
    assert forest["origin"] == "llm" and forest["options"]["reference_line"] == 0 and forest["layout"]["w"] == 2
    sc = clean["blocks"][1]
    assert sc["template"] == "scatter" and sc["bindings"]["x"] == {"column": "Avg_Perf_Human"}        # found by its label
    assert sc["transform"]["filter"] == [{"column": "Perf_Dir", "op": "eq", "value": "Up"}]
    assert sc["sufficiency"]["status"] == "insufficient" and sc["sufficiency"]["n_rows"] == 1          # one accuracy row < 3
    bad = clean["blocks"][2]
    assert bad["sufficiency"]["status"] == "insufficient" and "Horizontal" in bad["sufficiency"]["reason"] and "x" not in bad["bindings"]
    assert len(clean["blocks"]) == 3 and len(report["dropped"]) == 3 and b
    assert any("read as 'forest'" in r for r in report["repairs"]) and any("no_such_column" in r for r in report["repairs"])
    assert [u["question"] for u in clean["unanswered"]] == ["q2"] and "task difficulty" in clean["unanswered"][0]["reason"]
    assert ds_spec.validate(None, tables)[0]["blocks"] == [] and ds_spec.validate({"blocks": "x"}, tables)[0]["blocks"] == []

    # the default dashboard validates cleanly on both presets and never shows an empty card
    spec, rep = dashboards.default_spec(conn, ds, owner=True)
    assert spec["blocks"] and all(x["sufficiency"]["status"] != "insufficient" and x["origin"] == "default" for x in spec["blocks"])
    assert {"stat_count", "forest", "rows_table"} <= {x["template"] for x in spec["blocks"]}
    ds2, _ = _seed(conn, INDIRECT, "masem-indirect", sess)
    spec2, _ = dashboards.default_spec(conn, ds2, owner=True)
    assert {"heatmap", "rows_table"} <= {x["template"] for x in spec2["blocks"]} and all(x["unit"] == "factor_loadings" for x in spec2["blocks"])
    for x in (ds, ds2):
        records.clear_dataset_documents(conn, x); records.delete_dataset(conn, x)
    conn.commit(); conn.close()


def test_dashboards_api_ownership_visibility_and_lifecycle() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"dash-api-{uuid.uuid4().hex[:6]}"; mine, other = {"X-Session-Id": sess}, {"X-Session-Id": f"other-{uuid.uuid4().hex[:6]}"}
    ds, _ = _seed(conn, HAC, "human-ai-collab", sess)
    c = TestClient(appmod.app)
    assert c.get("/api/analysis/templates").json()["templates"]
    assert c.post("/api/dashboards/validate", json={"dataset_id": ds}, headers=other).status_code == 404
    default = c.post("/api/dashboards/validate", json={"dataset_id": ds}, headers=mine).json()
    assert default["default"] is True and default["spec"]["blocks"]
    # building is account work: the anonymous session that owns the dataset is asked to sign in
    assert c.post("/api/dashboards", json={"dataset_id": ds, "spec": default["spec"]}, headers=mine).status_code == 401
    uid = c.post("/api/auth/register", json={"email": f"dash-{uuid.uuid4().hex[:8]}@example.org", "password": "dash-test-pass-1"}, headers=mine).json()["user"]["id"]
    other_c = TestClient(appmod.app)                     # a second account for "someone else"
    other_c.post("/api/auth/register", json={"email": f"dash-o-{uuid.uuid4().hex[:8]}@example.org", "password": "dash-test-pass-1"}, headers=other)
    assert c.post("/api/dashboards", json={"dataset_id": ds, "spec": {"blocks": []}}, headers=mine).status_code == 422
    made = c.post("/api/dashboards", json={"dataset_id": ds, "title": "My board", "spec": default["spec"],
                                           "proposal": {"model": "m", "api_key": "sk-must-not-be-stored"}}, headers=mine).json()
    did = made["id"]
    assert made["rev"] == 1 and made["visibility"] == "private" and "api_key" not in (made["proposal"] or {})
    assert other_c.get(f"/api/dashboards/{did}", headers=other).status_code == 404
    got = c.get(f"/api/dashboards/{did}", headers=mine).json()
    assert got["can_edit"] is True and "session_id" not in got
    assert got["author"] is None                                   # an anonymous session has no name to show
    # what every dashboard states under its title comes with the analysis table
    meta = c.get(f"/api/datasets/{ds}/analysis", headers=mine).json()["dataset"]
    assert meta["n_papers"] >= 1 and meta["data_updated_at"] and meta["author"] is None and meta["published_url"] is None
    assert meta["preset"] == {"id": "human-ai-collab", "title": meta["preset"]["title"]} and meta["credibility"]["tier"]
    # without a title of its own, a saved dashboard takes the proposed one
    titled = c.post("/api/dashboards", json={"dataset_id": ds, "spec": {**default["spec"], "title": "  Does AI   help? "}}, headers=mine).json()
    assert titled["title"] == "Does AI help?" and titled["spec"]["title"] == "Does AI help?"
    assert c.delete(f"/api/dashboards/{titled['id']}", headers=mine).status_code == 200
    assert [d["id"] for d in c.get(f"/api/dashboards?dataset={ds}", headers=mine).json()["dashboards"]] == [did]
    assert [d["id"] for d in c.get("/api/dashboards", headers=mine).json()["dashboards"]] == [did]
    # edit with the right revision; a stale one is refused
    spec = copy.deepcopy(got["spec"]); spec["blocks"] = spec["blocks"][:2]
    ok = c.patch(f"/api/dashboards/{did}", json={"rev": 1, "title": "Renamed", "spec": spec}, headers=mine).json()
    assert ok["rev"] == 2 and ok["title"] == "Renamed" and len(ok["spec"]["blocks"]) == 2
    assert c.patch(f"/api/dashboards/{did}", json={"rev": 1, "title": "stale"}, headers=mine).status_code == 409
    assert other_c.patch(f"/api/dashboards/{did}", json={"rev": 2, "title": "x"}, headers=other).status_code == 404
    assert c.patch(f"/api/dashboards/{did}", json={"rev": 2, "visibility": "public"}, headers=mine).status_code == 409   # going public = publish (test_dashboard_publish)

    # a public dashboard from before releases existed: visible when dashboard AND dataset are public, still live
    dashboards.update(conn, did, rev=2, visibility="public"); conn.commit()
    assert other_c.get(f"/api/dashboards/{did}", headers=other).status_code == 404
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    seen = other_c.get(f"/api/dashboards/{did}", headers=other).json()
    assert seen["can_edit"] is False and "proposal" not in seen and seen["view"] == "live" and "rev" not in seen
    # a visitor may build their own dashboard over a public dataset; deleting is owner-only
    theirs = other_c.post("/api/dashboards", json={"dataset_id": ds, "spec": default["spec"]}, headers=other).json()["id"]
    assert other_c.delete(f"/api/dashboards/{did}", headers=other).status_code == 404
    assert other_c.delete(f"/api/dashboards/{theirs}", headers=other).json() == {"deleted": 1}
    # the dashboard dies with its dataset
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.commit()
    assert dashboards.get(conn, did) is None
    conn.close()
