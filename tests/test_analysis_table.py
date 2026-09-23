"""The analysis table: a dataset flattened to the row unit a dashboard plots (entry, sub-entry,
entry-level table row, table row inside a sub-entry), typed columns with roles, derived
per-row effect sizes, per-row provenance, and the evidence of every cell resolved
exact → row → table → entry. Skips without Postgres (the pure parts run regardless)."""
from __future__ import annotations

import copy
import json
import math
import os
import sys
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import analysis_table as at, presets, records  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


INDIRECT = {
    "paper_metadata": {"title": "Loadings paper", "doi": None, "year": 2016, "authors": ["Jin C-H"], "journal": "CHB"},
    "samples": [{"sample_id": "total", "n": 288, "country": "Korea", "lang": "Korean", "nfac": 1, "cfa": 0,
                 "factor_loadings": [{"item": k + 1, "factor": "F1", "loading": v} for k, v in enumerate([0.699, ".743", 0.775])],
                 "factor_correlations": []}],
    "evidence": [{"snippet": "Table 1 The EFA of innovativeness and NFC.", "page": 6, "source": "Table 1", "field": "samples[0].factor_loadings"},
                 {"snippet": ".775 Thinking is not my idea of fun.", "page": 6, "source": "Table 1", "field": "samples[0].factor_loadings[2]"},
                 {"snippet": "conducted using 288 college and graduate students", "page": 4, "source": None, "field": "samples[0].n"},
                 {"snippet": "Sample: total", "page": 4, "source": None, "field": "samples[0]"}]}

HAC = {
    "paper_metadata": {"title": "Teams paper", "doi": "10.1/x", "year": 2024, "authors": ["Woelfle T", "Hirt J", "Janiaud P"], "journal": "J"},
    "experiments": [{"Exp_ID": "1", "Task_Desc": "screening", "conditions": [
        {"Condition_Name": "with AI", "AI_Type": "LLM", "measures": [
            {"Perf_Metric": "accuracy", "Perf_Dir": "Up", "N_Human": 20, "N_AI": 5, "N_HumanAI": 20,
             "Avg_Perf_Human": 0.70, "Avg_Perf_AI": 0.80, "Avg_Perf_HumanAI": 0.78,
             "Sd_Perf_Human": 0.10, "Sd_Perf_AI": 0.05, "Sd_Perf_HumanAI": 0.08},
            {"Perf_Metric": "error rate", "Perf_Dir": "Down", "N_Human": 20, "N_AI": 5, "N_HumanAI": 20,
             "Avg_Perf_Human": 0.30, "Avg_Perf_AI": 0.20, "Avg_Perf_HumanAI": 0.22,
             "Sd_Perf_Human": 0.10, "Sd_Perf_AI": 0.05, "Sd_Perf_HumanAI": 0.08}]},
        {"Condition_Name": "no measures yet", "AI_Type": "LLM", "measures": []}]}],
    "evidence": [{"snippet": "accuracy 0.70 0.80 0.78", "page": 5, "source": "Table 3", "field": "experiments[0].conditions[0].measures[0]"},
                 {"snippet": "participants used the LLM", "page": 3, "source": None, "field": "experiments[0].conditions[0]"}]}


def test_units_and_flattening_per_preset() -> None:
    ind, hac, direct = (presets.load_all()[k] for k in ("masem-indirect", "human-ai-collab", "masem-direct"))
    assert [u["id"] for u in at.units(ind)] == ["entries", "factor_loadings", "factor_correlations"]
    assert [u["id"] for u in at.units(hac)] == ["entries", "conditions", "conditions.measures"]
    assert [u["id"] for u in at.units(direct)] == ["entries", "records"]
    assert at.default_unit(ind) == "factor_loadings" and at.default_unit(hac) == "conditions.measures" and at.default_unit(direct) == "records"

    fv = ingest(copy.deepcopy(INDIRECT), entries_key="samples").records[0].field_values
    rec = [{"idx": 0, "field_values": fv, "entry_index": 0, "sys": {"_study": "Jin (2016)", "_status": "verified"}}]
    rows = at.flatten(ind, at._unit(ind, "factor_loadings"), rec)
    assert [r["p"] for r in rows] == ["factor_loadings[0]", "factor_loadings[1]", "factor_loadings[2]"]
    assert rows[1]["vals"]["loading"] == 0.743 and rows[1]["vals"]["n"] == 288 and rows[1]["vals"]["_study"] == "Jin (2016)"   # coerced; parent repeated
    assert [r["p"] for r in at.flatten(ind, at._unit(ind, "factor_correlations"), rec)] == [""]          # empty table → the entry still has a row
    cols = {c["name"]: c for c in at.catalogue(ind, at._unit(ind, "factor_loadings"), rows)}
    assert cols["loading"]["roles"] == ["measure"] and cols["loading"]["min"] == 0.699 and cols["loading"]["n"] == 3
    assert "dimension" in cols["item"]["roles"] and "dimension" in cols["factor"]["roles"] and cols["notes"]["roles"] == []
    assert cols["_study"]["scope"] == "system" and cols["n"]["scope"] == "entry" and cols["loading"]["scope"] == "row"

    hfv = ingest(copy.deepcopy(HAC), entries_key="experiments").records[0].field_values
    hrows = at.flatten(hac, at._unit(hac, None), [{"idx": 0, "field_values": hfv, "entry_index": 0, "sys": {}}])
    assert [r["p"] for r in hrows] == ["conditions[0].measures[0]", "conditions[0].measures[1]", "conditions[1]"]
    assert hrows[0]["vals"]["Condition_Name"] == "with AI" and hrows[0]["vals"]["Task_Desc"] == "screening"


def test_derived_effect_sizes_match_hand_computation() -> None:
    hac, direct = presets.load_all()["human-ai-collab"], presets.load_all()["masem-direct"]
    hfv = ingest(copy.deepcopy(HAC), entries_key="experiments").records[0].field_values
    rows = at.flatten(hac, at._unit(hac, None), [{"idx": 0, "field_values": hfv, "entry_index": 0, "sys": {}}])
    sp = math.sqrt((19 * 0.08 ** 2 + 19 * 0.10 ** 2) / 38); j = 1 - 3 / (4 * 38 - 1); g = j * (0.78 - 0.70) / sp
    se = math.sqrt(40 / 400 + g ** 2 / 80)
    v = rows[0]["vals"]
    assert abs(v["g_team_vs_human"] - g) < 1e-5 and abs(v["g_team_vs_human_lo"] - (g - 1.959964 * se)) < 1e-5
    assert rows[1]["vals"]["g_team_vs_human"] > 0                       # error rate fell: flipped so that positive = better
    assert rows[2]["vals"]["g_team_vs_human"] is None                   # no measures: nothing computed
    cols = {c["name"]: c for c in at.catalogue(hac, at._unit(hac, None), rows)}
    assert cols["g_team_vs_human"]["roles"] == ["measure"] and cols["g_team_vs_human_lo"]["roles"] == ["bound"]
    assert "Avg_Perf_HumanAI" in cols["g_team_vs_human"]["derived"]["inputs"] and "Hedges" in cols["g_team_vs_human"]["help"]

    d = {"samples": [{"sample_id": "s", "records": [{"var1": "a", "var2": "b", "es": 0.30, "type": "r", "n": 103},
                                                    {"var1": "a", "var2": "c", "es": 1.8, "type": "or", "n": 103}]}]}
    dfv = ingest(d, entries_key="samples").records[0].field_values
    drows = at.flatten(direct, at._unit(direct, None), [{"idx": 0, "field_values": dfv, "entry_index": 0, "sys": {}}])
    lo, hi = math.tanh(math.atanh(0.3) - 1.959964 / 10), math.tanh(math.atanh(0.3) + 1.959964 / 10)
    assert abs(drows[0]["vals"]["es_ci_lo"] - lo) < 1e-5 and abs(drows[0]["vals"]["es_ci_hi"] - hi) < 1e-5
    assert drows[1]["vals"]["es_ci_lo"] is None                         # an odds ratio gets no Fisher interval


def _seed(conn, result, preset_id, sess):
    run = presets.resolve_run(conn, preset_id=preset_id, params={})
    with conn.transaction():
        records.upsert_schema(conn, run.schema_id, run.field_defs)
    doc = records.persist(conn, ingest(copy.deepcopy(result), entries_key=run.spec["entries"]["key"]), schema_id=run.schema_id,
                          source_job_id="analysis", session_id=sess, filename="secret-name.pdf",
                          extraction={"model": "gemini-x", "resolved_model": "gemini-x-001", "date": "2026-08-01T10:00:00"})
    ds = records.create_dataset(conn, title="Analysis set", schema_id=run.schema_id, session_id=sess)
    records.assign_document_to_dataset(conn, ds["id"], doc); conn.commit()
    return ds["id"], doc


def _seed_into(conn, result, preset_id, sess, dataset_id):
    """One more paper into an existing dataset (the "add a paper" step of the update workflow)."""
    run = presets.resolve_run(conn, preset_id=preset_id, params={})
    doc = records.persist(conn, ingest(copy.deepcopy(result), entries_key=run.spec["entries"]["key"]), schema_id=run.schema_id,
                          source_job_id="analysis", session_id=sess, filename="second-secret.pdf",
                          extraction={"model": "gemini-x", "resolved_model": "gemini-x-001", "date": "2026-09-01T10:00:00"})
    records.assign_document_to_dataset(conn, dataset_id, doc); conn.commit()
    return doc


def test_table_rows_provenance_and_cell_evidence() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    sess = f"ana-{uuid.uuid4().hex[:6]}"
    ds, doc = _seed(conn, INDIRECT, "masem-indirect", sess)
    t = at.build(conn, ds, None, owner=True)
    assert t["unit"]["id"] == "factor_loadings" and t["n_rows"] == 3 and [u["id"] for u in t["units"]][1] == "factor_loadings"
    names = [c["name"] for c in t["columns"]]
    rec = t["records"][0]
    assert rec["model"] == "gemini-x-001" and rec["extracted_at"] == "2026-08-01" and rec["status"] == "unverified"
    assert rec["filename"] == "secret-name.pdf" and t["papers"][0]["study"] == "Jin (2016)"
    assert t["rows"][2]["v"][names.index("loading")] == 0.775 and t["rows"][2]["p"] == "factor_loadings[2]"
    assert t["dataset"]["credibility"]["tier"] and t["as_of"]
    public = at.build(conn, ds, None, owner=False)["records"][0]
    assert "filename" not in public and "document_id" not in public          # the bright wall

    rid = rec["id"]
    cell = lambda path, col: {"record_id": rid, "path": path, "column": col}   # noqa: E731
    ev = {(c["path"], c["column"]): c for c in at.cell_evidence(conn, ds, "factor_loadings", [
        cell("factor_loadings[2]", "loading"), cell("factor_loadings[0]", "loading"), cell("factor_loadings[0]", "n"),
        cell("factor_loadings[0]", "country"), cell("factor_loadings[0]", "_study"),
        {"record_id": str(uuid.uuid4()), "path": "", "column": "n"}])}
    assert ev[("factor_loadings[2]", "loading")]["kind"] == "row" and ".775" in ev[("factor_loadings[2]", "loading")]["items"][0]["snippet"]
    assert ev[("factor_loadings[0]", "loading")]["kind"] == "table" and ev[("factor_loadings[0]", "loading")]["items"][0]["source"] == "Table 1"
    assert ev[("factor_loadings[0]", "n")]["kind"] == "exact" and ev[("factor_loadings[0]", "n")]["items"][0]["page"] == 4
    assert ev[("factor_loadings[0]", "country")]["kind"] == "entry"          # nothing closer than the entry's own quote
    assert ev[("factor_loadings[0]", "_study")]["kind"] == "none" and len(ev) == 5   # a foreign record id is ignored
    assert all(set(i) == {"snippet", "page", "source"} for c in ev.values() for i in c["items"])   # no geometry leaves

    # a human correction is flagged on the row and reported with the evidence
    fv = copy.deepcopy(records.document_view(conn, doc)["records"][0]["field_values"]); fv["factor_loadings"][0]["loading"] = 0.7
    records.verify_record(conn, rid, status="verified", field_values=fv,
                          diff=[{"field_path": "factor_loadings[0].loading", "original_value": 0.699, "final_value": 0.7}])
    t2 = at.build(conn, ds, None, owner=True)
    assert t2["rows"][0]["c"] == [names.index("loading")] and "c" not in t2["rows"][1] and t2["records"][0]["status"] == "verified"
    c = at.cell_evidence(conn, ds, "factor_loadings", [cell("factor_loadings[0]", "loading")])[0]
    assert c["corrected"]["original_value"] == 0.699 and c["corrected"]["final_value"] == 0.7

    # derived values rest on their inputs
    ds2, _ = _seed(conn, HAC, "human-ai-collab", sess)
    rid2 = at.build(conn, ds2, None, owner=True)["records"][0]["id"]
    d = at.cell_evidence(conn, ds2, None, [{"record_id": rid2, "path": "conditions[0].measures[0]", "column": "g_team_vs_human"}])[0]
    assert d["kind"] == "derived" and "Hedges" in d["formula"] and {i["kind"] for i in d["inputs"]} == {"row"}
    for x in (ds, ds2):
        records.clear_dataset_documents(conn, x); records.delete_dataset(conn, x)
    conn.commit(); conn.close()


def test_analysis_endpoints_follow_the_dataset_gate() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    from fastapi.testclient import TestClient
    from paperlens import app as appmod
    conn = records.connect(); records.init_db(conn)
    sess = f"ana-gate-{uuid.uuid4().hex[:6]}"
    ds, _doc = _seed(conn, INDIRECT, "masem-indirect", sess)
    c = TestClient(appmod.app); mine, other = {"X-Session-Id": sess}, {"X-Session-Id": "a-stranger"}
    assert c.get(f"/api/datasets/{ds}/analysis", headers=other).status_code == 404
    assert c.post(f"/api/datasets/{ds}/analysis/evidence", json={"cells": []}, headers=other).status_code == 404
    t = c.get(f"/api/datasets/{ds}/analysis", headers=mine).json()
    assert t["viewer"]["owner"] is True and t["n_rows"] == 3 and t["records"][0]["filename"] == "secret-name.pdf"
    assert c.get(f"/api/datasets/{ds}/analysis?unit=entries", headers=mine).json()["n_rows"] == 1
    records.set_dataset_visibility(conn, ds, "public"); conn.commit()
    pub = c.get(f"/api/datasets/{ds}/analysis", headers=other).json()
    assert pub["viewer"]["owner"] is False and "filename" not in pub["records"][0]
    ev = c.post(f"/api/datasets/{ds}/analysis/evidence", headers=other,
                json={"unit": "factor_loadings", "cells": [{"record_id": pub["records"][0]["id"], "path": "factor_loadings[2]", "column": "loading"}]}).json()
    assert ev["cells"][0]["kind"] == "row" and ev["cells"][0]["items"][0]["page"] == 6      # a public viewer reads the quote
    assert '"rect"' not in json.dumps(ev) and "document_id" not in json.dumps(ev)
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.commit(); conn.close()


def test_only_complete_rows_are_analysed_and_the_rest_is_counted() -> None:
    """human-ai-collab declares display.analysis.complete: a measure needs a human-alone, an
    AI-alone and a human+AI mean. Incomplete rows stay in the dataset but no layout shows them;
    an experiment without any complete measure goes too, and the table says what was left out."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    import copy
    conn = records.connect(); records.init_db(conn)
    doc = copy.deepcopy(HAC)
    doc["experiments"][0]["conditions"][0]["measures"].append({"Perf_Metric": "time", "Perf_Dir": "Down", "Avg_Perf_Human": 60, "Avg_Perf_HumanAI": 40})
    doc["experiments"].append({"Exp_ID": "2", "Task_Desc": "annotation", "conditions": [
        {"Condition_Name": "verify", "AI_Type": "LLM", "measures": [{"Perf_Metric": "accuracy", "Avg_Perf_AI": 0.6, "Avg_Perf_HumanAI": 0.7}]}]})
    ds, _ = _seed(conn, doc, "human-ai-collab", f"cmp-{uuid.uuid4().hex[:6]}")
    t = at.build(conn, ds, "conditions.measures", owner=True)
    names = [c["name"] for c in t["columns"]]
    assert [row["v"][names.index("Perf_Metric")] for row in t["rows"]] == ["accuracy", "error rate"]
    assert t["dataset"]["left_out"] == {"rows": 3, "of": 5, "papers": 0, "unit": t["unit"]["label"],
                                        "needs": "a human-alone, an AI-alone and a human+AI mean"}
    assert {u["id"]: u["n"] for u in t["units"]} == {"entries": 1, "conditions": 1, "conditions.measures": 2}
    assert len(at.build(conn, ds, "entries", owner=True)["rows"]) == 1          # experiment 2 has no complete measure
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.commit(); conn.close()


def test_a_dataset_follows_its_records_when_the_preset_gained_a_field() -> None:
    """A preset that gains a field mints a new schema version. Once EVERY record of the dataset
    carries that version (re-extracted, re-imported), the analysis table reads them with it —
    otherwise the new columns would stay invisible. Mixed versions keep the dataset's own."""
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    ds, _doc = _seed(conn, HAC, "human-ai-collab", f"ver-{uuid.uuid4().hex[:6]}")
    old = at.live_source(conn, ds)["schema_id"]
    newer = f"{old.partition('@')[0]}@ffffffff"
    with conn.transaction():
        conn.execute("INSERT INTO schema (id, field_defs) SELECT %s, field_defs FROM schema WHERE id = %s ON CONFLICT (id) DO NOTHING", (newer, old))
        conn.execute("UPDATE record SET schema_id = %s WHERE dataset_id = %s::uuid", (newer, ds))
    assert at.live_source(conn, ds)["schema_id"] == newer                      # all records agree: follow them
    with conn.transaction():
        conn.execute("UPDATE record SET schema_id = %s WHERE dataset_id = %s::uuid AND entry_index = 0", (old, ds))
    assert at.live_source(conn, ds)["schema_id"] == old                        # mixed: the dataset's own, as before
    other = f"other-preset@{uuid.uuid4().hex[:8]}"
    with conn.transaction():
        conn.execute("INSERT INTO schema (id, field_defs) SELECT %s, field_defs FROM schema WHERE id = %s", (other, old))
        conn.execute("UPDATE record SET schema_id = %s WHERE dataset_id = %s::uuid", (other, ds))
    assert at.live_source(conn, ds)["schema_id"] == old                        # another preset is never silently adopted
    records.clear_dataset_documents(conn, ds); records.delete_dataset(conn, ds); conn.close()
