"""A chained extraction end to end, offline: a generated PDF, a fake model that answers the
k runs differently, a fake follow-up that adjudicates by reference. Checks what is stored
(runs hidden, artifacts once, the final with real quotes and rects), what the review sees
(support, candidates, flags, trace, a read-only run through its final's pages), and what
every listing, dataset and delete path does with run documents. Needs Postgres."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

from paperlens import chain, extract, preset_spec, records, storage  # noqa: E402
from test_chain_spec import BASE  # noqa: E402

PAGES = ["Parental death reduces earnings by 4 percent in the first year after the loss.",
         "Import competition lowered employment in textiles.\nTable 2 reports -1.3 for the main effect.",
         "Higher tax rates increase evasion among the self-employed."]


def _pdf() -> bytes:
    import fitz
    d = fitz.open()
    for text in PAGES:
        pg = d.new_page()
        for i, line in enumerate(text.split("\n")):
            pg.insert_text((72, 90 + 16 * i), line, fontsize=11)
    out = d.tobytes(); d.close(); return out


def _claim(cause, effect, sign, est=None):
    return {"cause": cause, "effect": effect, "sign": sign, "statement": f"{cause} changes {effect}", "tags": [],
            "results": [{"estimate": est}] if est is not None else []}


PM = {"title": "Chained paper", "doi": None, "year": 2021, "authors": ["A B"], "journal": None}
Q0 = {"snippet": "Parental death reduces earnings by 4 percent", "page": 1, "source": None, "field": "claims[0]"}
Q1 = {"snippet": "Import competition lowered employment in textiles", "page": 2, "source": None, "field": "claims[1]"}
Q1E = {"snippet": "-1.3", "page": 2, "source": "Table 2", "field": "claims[1].results[0].estimate"}
Q2 = {"snippet": "Higher tax rates increase evasion", "page": 3, "source": None, "field": "claims[2]"}
RUNS = [
    {"paper_metadata": PM, "claims": [_claim("parental death", "earnings", "-", -0.04), _claim("import competition", "employment", "+", -1.3)],
     "evidence": [Q0, Q1, Q1E]},
    {"paper_metadata": PM, "claims": [_claim("Parental death", "earnings", "-", -0.04), _claim("import competition", "employment", "-", -1.3)],
     "evidence": [Q0, Q1, Q1E]},
    {"paper_metadata": PM, "claims": [_claim("parental death", "earnings", "-"), _claim("import competition", "employment", "-", -1.3),
                                      _claim("tax rate", "evasion", "+")],
     "evidence": [Q0, Q1, Q2]},
]
ADJ = {"claims": [_claim("parental death", "earnings", "-", -0.04), _claim("import competition", "employment", "-", -1.3),
                  _claim("tax rate", "evasion", "+")],
       "evidence": [{"ref": "r1.claims[0]", "field": "claims[0]"},
                    {"ref": ["r2.claims[1]", "r3.claims[1]"], "field": "claims[1]"},
                    {"ref": "r1.claims[1].sign", "field": "claims[1].sign"},
                    {"ref": "r3.claims[2]", "field": "claims[2]"}]}


def _spec(k=3, calls=True):
    d = copy.deepcopy(BASE)
    d["chain"] = {"k": k, "identity": ["cause", "effect"], "aggregate": {"kind": "vote"},
                  "calls": [{"id": "adjudicate", "prompt": {"text": "Adjudicate the runs.\n${previous}\n${output_schema}\n${evidence}\n${return_format}"}}] if calls else []}
    return preset_spec.normalize(d)


def _db_ok() -> bool:
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


class _Fake:
    """The model: one run document per call, in order; ``fail`` raises on every call."""
    def __init__(self, docs, fail: Exception | None = None):
        self.docs, self.fail, self.calls = docs, fail, 0

    def __call__(self, pdf_bytes, prompt, **kw):
        self.calls += 1
        if self.fail:
            raise self.fail
        return extract.LLMResult(text=json.dumps(self.docs[(self.calls - 1) % len(self.docs)]),
                                 finish_reason="stop", usage={"total": 100}, resolved_model="fake-2026")


def _setup(conn, spec):
    sid = preset_spec.schema_id(spec)
    with conn.transaction():
        records.upsert_schema(conn, sid, preset_spec.field_defs_for(spec))
    return sid


def test_chain_runs_follow_up_and_final(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    monkeypatch.setattr(chain, "RETRY_DELAY", 0)
    conn = records.connect(); records.init_db(conn)
    spec = _spec(); sid = _setup(conn, spec); sess = f"ch-{uuid.uuid4().hex[:6]}"
    seen = {}

    def fake_generate(prompt, *, model, api_key, base_url=None):
        seen["prompt"] = prompt
        return json.dumps(ADJ)

    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        out = extract.run(conn, _pdf(), "EXTRACT PROMPT", model="fake", api_key="k", schema_id=sid, session_id=sess,
                          spec=spec, params={}, complete=_Fake(RUNS), generate=fake_generate, store=store, filename="chain.pdf")
        final = out["document_id"]
        assert out["n_calls"] == 4 and len(out["run_document_ids"]) == 3 and out["fallback"] is None
        # the follow-up saw the runs under stable ids, with their quotes, and the by-reference rules
        assert "r1.claims[0]" in seen["prompt"] and "r3.claims[2]" in seen["prompt"]
        assert "Parental death reduces earnings" in seen["prompt"] and "by reference" in seen["prompt"]
        # the group: three hidden runs and one final, artifacts stored once
        rows = conn.execute("SELECT id::text, role, pass_index, group_id::text FROM extraction_document WHERE group_id = "
                            "(SELECT group_id FROM extraction_document WHERE id = %s::uuid) ORDER BY (role = 'final'), pass_index", (final,)).fetchall()
        assert [(r[1], r[2]) for r in rows] == [("run", 0), ("run", 1), ("run", 2), ("final", None)]
        assert len({r[3] for r in rows}) == 1 == len({r[3] for r in rows} & {out["group_id"]})
        for rid, role, _, _ in rows:
            assert store.exists(storage.raw_key(rid))
            assert store.exists(storage.pdf_key(rid)) is (role == "final")
            assert store.exists(storage.page_image_key(rid, 1)) is (role == "final")
        assert store.exists(storage.call_raw_key(final, 0)) and store.exists(storage.call_raw_key(final, 3))
        assert store.exists(storage.chain_key(final))
        # the final's raw file is the RESOLVED document: real quotes, run-labelled sources
        raw = json.loads(store.get(storage.raw_key(final)).decode("utf-8"))
        assert [c["sign"] for c in raw["claims"]] == ["-", "-", "+"]
        assert all("snippet" in e and e["page"] for e in raw["evidence"])
        assert any(e["source"].startswith("Table 2 · ") for e in raw["evidence"])
        assert raw["paper_metadata"]["title"] == "Chained paper"
        # what the review sees
        view = records.document_view(conn, final, store=store)
        assert view["role"] == "final" and view["read_only"] is False and view["chain"]["k"] == 3
        assert [c["id"] for c in view["chain"]["calls"]] == ["r1", "r2", "r3", "adjudicate"]
        recs = view["records"]
        assert recs[1]["chain"]["support"] == {"n": 3, "k": 3}
        assert [c["value"] for c in recs[1]["chain"]["candidates"]["sign"]] == ["+", "-"]
        assert "contested:sign" in recs[1]["chain"]["flags"]
        assert recs[2]["chain"]["support"] == {"n": 1, "k": 3} and "minority" in recs[2]["chain"]["flags"]
        assert any(t["ref"] == "r1.claims[1].sign" for t in recs[1]["chain"]["trace"])
        assert any(e["rect"] for e in view["evidence"]), "the inherited quotes were located on the PDF"
        assert len(view["pages"]) == 3
        # a run: read-only, pages through the final, its own quotes located too
        run0 = rows[0][0]
        rv = records.document_view(conn, run0, store=store)
        assert rv["read_only"] is True and rv["role"] == "run" and rv["pages_document_id"] == final
        assert len(rv["pages"]) == 3 and rv["pages"][0]["url"].startswith(store.url(storage.page_image_key(final, 1)))
        assert any(e["rect"] for e in rv["evidence"])
        assert records.pdf_document_id(conn, run0) == final and records.document_role(conn, run0) == "run"
        group = records.chain_group(conn, final)
        assert [r["label"] for r in group["runs"]] == ["r1", "r2", "r3"] and group["final_id"] == final
        # listings and the trial meter see one paper; a run never joins a dataset, the final does
        assert [x["document_id"] for x in records.list_documents(conn, session_id=sess)] == [final]
        assert records.count_documents_for_session(conn, sess) == 1
        ds = records.create_dataset(conn, title="chain set", schema_id=sid, session_id=sess)["id"]
        with pytest.raises(ValueError):
            records.assign_document_to_dataset(conn, ds, run0)
        assert records.assign_document_to_dataset(conn, ds, final) == 3
        assert records.dataset_duplicates(conn, ds) == [] or all(len(g) == 1 for g in records.dataset_duplicates(conn, ds))
        assert "candidates" not in json.dumps(records.dataset_export(conn, ds))
        # adopting a candidate is a logged correction naming the field
        fv = copy.deepcopy(recs[1]["field_values"]); fv["sign"] = "+"
        records.verify_record(conn, recs[1]["id"], status="corrected", field_values=fv,
                              diff=[{"field_path": "sign", "original_value": "-", "final_value": "+"}])
        ev = records.record_events(conn, recs[1]["id"])
        assert ev[0]["diff"][0]["field_path"] == "sign" and ev[0]["status"] == "corrected"
        assert records.get_record(conn, recs[1]["id"])["verification_status"] == "unverified"
        # a run alone cannot be deleted; the final takes its runs along
        assert records.delete_document(conn, run0) == {"deleted": 0, "refused": "run"}
        assert records.delete_document(conn, final)["deleted"] == 1
        assert conn.execute("SELECT count(*) FROM extraction_document WHERE group_id = %s::uuid", (out["group_id"],)).fetchone()[0] == 0
        records.delete_dataset(conn, ds)
    conn.commit(); conn.close()


def test_single_call_presets_take_the_legacy_path_and_failures_clean_up(monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    monkeypatch.setattr(chain, "RETRY_DELAY", 0)
    conn = records.connect(); records.init_db(conn)
    sess = f"ch-{uuid.uuid4().hex[:6]}"
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        plain = _spec(k=1, calls=False); sid = _setup(conn, plain)
        out = extract.run(conn, _pdf(), "P", model="fake", api_key="k", schema_id=sid, session_id=sess,
                          spec=plain, params={}, complete=_Fake(RUNS[:1]), store=store)
        row = conn.execute("SELECT role, group_id, chain FROM extraction_document WHERE id = %s::uuid", (out["document_id"],)).fetchone()
        assert row == ("final", None, None) and "n_calls" not in out
        # a follow-up that fails: the runs are voted instead, and the document says so
        spec = _spec(); sid = _setup(conn, spec)

        def broken(prompt, **kw):
            raise RuntimeError("provider down")
        out2 = extract.run(conn, _pdf(), "P", model="fake", api_key="k", schema_id=sid, session_id=sess,
                           spec=spec, params={}, complete=_Fake(RUNS), generate=broken, store=store)
        assert out2["fallback"]["call"] == "adjudicate" and out2["n_calls"] == 4
        v = records.document_view(conn, out2["document_id"], store=store)
        assert v["chain"]["fallback"]["error"] == "provider down"
        assert all("fallback" in r["chain"]["flags"] and r["chain"]["kind"] == "vote" for r in v["records"])
        assert [c["sign"] for c in (r["field_values"] for r in v["records"])] == ["-", "-", "+"]   # the vote: 2 of 3 say −
        # every run failing leaves nothing behind
        with pytest.raises(RuntimeError):
            extract.run(conn, _pdf(), "P", model="fake", api_key="k", schema_id=sid, session_id=f"{sess}-x",
                        spec=spec, params={}, complete=_Fake(RUNS, fail=ValueError("bad key")), store=store)
        assert conn.execute("SELECT count(*) FROM extraction_document WHERE session_id = %s", (f"{sess}-x",)).fetchone()[0] == 0
        for did in (out["document_id"], out2["document_id"]):
            records.delete_document(conn, did)
    conn.commit(); conn.close()
