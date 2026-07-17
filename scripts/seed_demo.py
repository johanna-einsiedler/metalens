"""Seed the local Postgres with demo data so you can poke the API by hand.

No LLM API key needed: it ingests two canonical fixtures, enriches a real DOI
live (Crossref/Unpaywall/OpenAlex), and runs ONE full extraction over a generated
PDF using a *fake* LLM (so the render → highlight → records → page-image-storage
chain runs for real). Then it prints exactly what to open.

    uv run python scripts/seed_demo.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

from paperlens import enrich, extract, records, storage  # noqa: E402
from paperlens.ingest import ingest  # noqa: E402
import fixtures  # noqa: E402

DEMO_DOI = "10.1257/jep.33.2.3"  # Acemoglu & Restrepo, "Automation and New Tasks"


def _make_pdf() -> bytes:
    import fitz
    pages = [
        "Study 1 included N = 147 participants from two universities.",
        "Methods. A two-factor solution was retained after rotation.",
        "Table 2. Rotated factor matrix.\nitem1   0.83   0.12\nitem2   0.45   0.71",
    ]
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        for i, line in enumerate(text.split("\n")):
            page.insert_text((72, 90 + 16 * i), line, fontsize=12)
    data = doc.tobytes(); doc.close()
    return data


_FAKE_JSON = """{
  "paper_metadata": {"title": "A demo factor-analysis study", "doi": "10.9999/demo.0001", "year": 2026, "authors": ["Demo A"]},
  "samples": [{
    "sample_id": "S1", "n": 147,
    "factor_loadings": {"item1": {"F1": 0.83, "F2": 0.12}, "item2": {"F1": 0.45, "F2": 0.71}},
    "evidence": [
      {"snippet": "N = 147 participants", "page": 1, "source": null, "field": "samples[0]"},
      {"snippet": "Table 2. Rotated factor matrix", "page": 3, "source": "Table 2", "field": "samples[0].factor_loadings"}
    ]
  }]
}"""


def _fake_complete(pdf_bytes, prompt, **kw):
    return extract.LLMResult(text=_FAKE_JSON, finish_reason="stop", resolved_model="fake-demo-model")


_SPINE_TABLES = ("paper", "paper_field_provenance", "schema", "extraction_document",
                 "record", "evidence_span", "field_confidence", "verification_event",
                 "dataset", "saved_view", "theme")


def main() -> None:
    conn = records.connect()
    records.init_db(conn)
    # Clean slate so the demo is deterministic (clears any leftover test data).
    conn.execute("TRUNCATE " + ", ".join(_SPINE_TABLES) + " RESTART IDENTITY CASCADE")
    conn.commit()
    print("• DB ready (paperlens), clean slate\n")

    # 1) ingest two canonical fixtures under named schemas
    docs = {}
    for name, raw, schema_id in [
        ("forestplot", fixtures.FORESTPLOT_JSON, "forestplot@v1"),
        ("masem_rich", fixtures.MASEM_RICH_JSON, "masem@v3"),
    ]:
        res = ingest(raw)
        docs[name] = records.persist(conn, res, schema_id=schema_id, source_job_id=f"demo-{name}")
        print(f"• ingested {name}: {len(res.records)} record(s), doi={res.doi}, schema={schema_id}")

    # 2) enrich a real DOI (live; degrades gracefully offline)
    print(f"\n• enriching {DEMO_DOI} (live Crossref/Unpaywall/OpenAlex) ...")
    try:
        out = enrich.enrich_paper(conn, DEMO_DOI)
        conn.commit()
        print(f"  -> {out['provenance_count']} fields enriched")
    except Exception as e:  # noqa: BLE001
        print(f"  -> enrichment skipped (offline?): {e!r}")

    # 3) full extraction over a generated PDF using a FAKE LLM (no key)
    print("\n• running a fake-LLM extraction over a generated 3-page PDF ...")
    store = storage.get_store()
    ex = extract.run_extraction(conn, _make_pdf(), prompt="demo", model="gpt-4o",
                                schema_id="masem@v3", session_id="demo-session",
                                complete=_fake_complete, store=store)
    conn.commit()
    print(f"  -> doc {ex['document_id'][:8]}: {ex['n_records']} record, "
          f"{ex['n_pages']} pages, {ex['n_highlights']} highlight(s)")

    # 4) a dataset (Phase 2) + a saved observatory view (Phase 4) over it
    ds = records.create_dataset(conn, title="Forest-plot demo dataset",
                                schema_id="forestplot@v1", session_id="demo-session",
                                visibility="public")
    records.assign_document_to_dataset(conn, ds["id"], docs["forestplot"])
    # verify one record so the credibility badge moves off AI-only (Phase 3)
    drecs = records.dataset_records(conn, ds["id"])
    if drecs:
        records.verify_record(conn, drecs[0]["id"], status="verified")
    view = records.create_view(conn, title="Studies by design", dataset_ids=[ds["id"]],
                               visibility="public",
                               viz_config={"kind": "bar", "group_by": "design", "measure": "count"})
    cred = records.dataset_credibility(conn, ds["id"])
    print(f"• dataset {ds['id'][:8]} + observatory view {view['id'][:8]} "
          f"(badge: {cred['label']})")
    conn.close()

    base = "http://127.0.0.1:8000"
    print("\n" + "=" * 70)
    print("SEED COMPLETE.  Now start the API and explore:\n")
    print("  uv run uvicorn paperlens.app:app --reload")
    print(f"\n  Interactive docs:  {base}/docs\n")
    print("  Try these read endpoints (no key needed):")
    print(f"    curl '{base}/api/presets'")
    print(f"    curl '{base}/api/schemas/masem@v3'")
    print(f"    curl '{base}/api/papers/lookup?doi=10.1037/abc.0000123'   # forestplot passport")
    print(f"    curl '{base}/api/papers/provenance?doi={DEMO_DOI}'        # enriched + per-field provenance")
    print(f"    curl '{base}/api/papers/{ex['paper_id']}/records'         # extracted records")
    print(f"\n  Open a rendered page image in the browser:")
    print(f"    {base}/artifacts/{ex['page_image_keys'][0]}")
    print(f"\n  Phase 2-4 surfaces:")
    print(f"    {base}/observatory                              # saved view -> live chart")
    print(f"    {base}/                                         # viewer (verify/flag records)")
    print(f"    curl '{base}/api/datasets/{ds['id']}'   # dataset + computed credibility badge")
    print(f"    curl '{base}/api/views/{view['id']}/data'  # the view, recomputed on read")
    print("=" * 70)


if __name__ == "__main__":
    main()
