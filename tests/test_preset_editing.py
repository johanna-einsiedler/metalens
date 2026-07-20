"""Editable study fields (set a constant across all records; edit paper identity) +
field_types passthrough into field_defs. Skips without Postgres."""
from __future__ import annotations

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import extract, presets, records, storage  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _make_pdf():
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), "human ai study"); b = d.tobytes(); d.close()
    return b


def _two(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
    return extract.LLMResult(text=json.dumps({
        "paper_metadata": {"title": "Orig Title", "doi": "10.1/edit", "year": 2021,
                           "journal": "Old Venue", "authors": ["A"]},
        "records": [
            {"AI_Type": "Deep", "Perf_Metric": "Accuracy", "Avg_Perf_HumanAI": 0.6},
            {"AI_Type": "Deep", "Perf_Metric": "F1", "Avg_Perf_HumanAI": 0.5},
        ],
        "evidence": [{"snippet": "Deep", "page": 1, "source": "-", "field": "records[0].AI_Type"}],
    }), finish_reason="stop", usage={"total": 5}, resolved_model="fake")


def test_study_field_edits() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    with tempfile.TemporaryDirectory() as d:
        out = extract.run_extraction(conn, _make_pdf(), prompt="x", model="gpt-4o", api_key="",
                                     schema_id="human-ai-collab@v1", session_id="sess-edit",
                                     complete=_two, store=storage.LocalObjectStore(root=d))
        conn.commit()
    doc_id = out["document_id"]

    # a study-constant edit applies to EVERY record and is logged as a correction on each
    assert records.set_field_across_records(conn, doc_id, "AI_Type", "Generative") == 2
    conn.commit()
    v = records.document_view(conn, doc_id)
    assert [r["field_values"]["AI_Type"] for r in v["records"]] == ["Generative", "Generative"]
    assert all(any(c["field_path"] == "AI_Type" for c in r["corrections"]) for r in v["records"])
    assert records.set_field_across_records(conn, doc_id, "AI_Type", "Generative") == 0   # idempotent
    conn.commit()

    # edit paper identity — only the allowlisted fields change
    pid = str(conn.execute("SELECT paper_id FROM extraction_document WHERE id=%s::uuid",
                           (doc_id,)).fetchone()[0])
    p = records.update_paper_fields(conn, pid, {"title": "New Title", "year": 2024,
                                                "authors": ["X", "Y"], "doi": "SHOULD_BE_IGNORED"})
    conn.commit()
    assert p["title"] == "New Title" and p["year"] == 2024 and p["authors"] == ["X", "Y"]
    assert p["doi"] == "10.1/edit"                       # not in the allowlist → unchanged
    assert records.update_paper_fields(conn, pid, {"nope": 1}) is None
    assert records.document_view(conn, doc_id)["paper"]["title"] == "New Title"


def test_field_types_passthrough() -> None:
    if not _db_ok():
        import pytest; pytest.skip("no Postgres")
    conn = records.connect(); records.init_db(conn)
    records.create_personal_preset(
        conn, preset_id="ft-test", session_id="ft-sess", title="FT", prompt="p",
        mode="extraction",
        sub_views=[{"id": "a", "label": "A", "include_keys": ["AI_Type", "Task_Data"]}],
        template_params={"field_types": {
            "AI_Type": {"type": "select", "options": ["Deep", "Generative"]},
            "Task_Data": {"type": "multiselect", "options": ["Text", "Image"]},
            "Perf_Metric": {"type": "select", "options": ["Accuracy"], "allow_other": True},
        }})
    conn.commit()
    ft = presets.emit_schema_row("ft-test", conn=conn)["field_types"]
    assert ft["AI_Type"]["type"] == "select" and "Generative" in ft["AI_Type"]["options"]
    assert ft["Task_Data"]["type"] == "multiselect"
    assert ft["Perf_Metric"].get("allow_other") is True
