"""WS7 — dataset materialization (exporter.py) for GitHub publishing.

materialize_dataset(dataset) → {metadata, results}; results.papers has one entry per
document, each carrying the exact publishable canonical JSON (round-trips through
reconstruct). Skips without Postgres.
"""
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

from paperlens import exporter, extract, records, storage  # noqa: E402


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def _make_pdf():
    import fitz
    d = fitz.open(); d.new_page().insert_text((72, 90), "value 0.604"); b = d.tobytes(); d.close()
    return b


def _fake(pdf, prompt, *, model="", api_key="", base_url=None, use_text=False):
    return extract.LLMResult(text=json.dumps({
        "paper_metadata": {"title": "Export Test", "doi": "10.1/x"},
        "records": [{"Paper_Name": "E_2026", "Avg_Perf_HumanAI": 0.604}],
        "evidence": [{"snippet": "0.604", "page": 1, "source": "T1", "field": "records[0].Avg_Perf_HumanAI"}],
    }), finish_reason="stop", usage={"total": 5}, resolved_model="fake")


def test_materialize_dataset() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    conn = records.connect(); records.init_db(conn)
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        out = extract.run_extraction(conn, _make_pdf(), prompt="x", model="gpt-4o", api_key="",
                                     schema_id="human-ai-collab@v1", session_id="sess-exp",
                                     complete=_fake, store=store)
        conn.commit()
    ds = records.create_dataset(conn, title="Export Test DS", schema_id="human-ai-collab@v1",
                                session_id="sess-exp", visibility="private", model="gpt-4o")
    records.assign_document_to_dataset(conn, ds["id"], out["document_id"])
    conn.commit()

    m = exporter.materialize_dataset(conn, ds["id"])
    assert m["metadata"]["title"] == "Export Test DS"
    assert m["metadata"]["schema_id"] == "human-ai-collab@v1"
    papers = m["results"]["papers"]
    assert len(papers) == 1
    result = papers[0]["result"]
    # publishable canonical JSON round-trips: paper_metadata + core array + evidence
    assert result["paper_metadata"]["title"] == "Export Test"
    assert isinstance(result.get("records"), list) and len(result["records"]) == 1
    assert result["records"][0]["Avg_Perf_HumanAI"] == 0.604
    assert result["evidence"][0]["field"] == "records[0].Avg_Perf_HumanAI"
    assert papers[0]["model"] in ("gpt-4o", "fake")
    conn.close()


def _main() -> int:
    failures = 0
    for label, fn in [("export:materialize", test_materialize_dataset)]:
        try:
            fn(); print(f"  PASS  {label}")
        except Exception as exc:  # noqa: BLE001
            if exc.__class__.__name__ == "Skipped":
                print(f"  SKIP  {label}: {exc}"); continue
            failures += 1; print(f"  FAIL  {label}: {exc!r}")
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
