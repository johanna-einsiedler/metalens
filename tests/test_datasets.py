"""Phase 2a — dataset entity: create -> assign a document's records -> read back.

Verifies persistence on a FRESH connection (commit discipline) and that listing
is scoped by principal (anonymous session_id here).
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fixtures  # noqa: E402

try:
    from paperlens import records
    from paperlens.ingest import ingest
    _OK = True
except Exception:
    _OK = False


def _db_ok() -> bool:
    if not _OK:
        return False
    try:
        c = records.connect(); c.close(); return True
    except Exception:
        return False


def run() -> int:
    if not _db_ok():
        print("  SKIP  no Postgres available")
        return 0

    conn = records.connect()
    records.init_db(conn)
    failures = 0

    # seed a document of records to put in a dataset
    res = ingest(fixtures.FORESTPLOT_JSON)
    doc_id = records.persist(conn, res, schema_id="forestplot@v1", source_job_id="ds-test")
    conn.commit()

    sess = "ds-test-session"
    ds = records.create_dataset(conn, title="Remote-work meta-analyses",
                                schema_id="forestplot@v1", session_id=sess, visibility="private")
    n = records.assign_document_to_dataset(conn, ds["id"], doc_id)
    conn.close()

    # FRESH connection: assert everything committed
    fresh = records.connect()
    try:
        recs = records.dataset_records(fresh, ds["id"])
        if len(recs) == n == 2:
            print(f"  PASS  create+assign  (dataset {ds['id'][:8]}, {len(recs)} records committed)")
        else:
            failures += 1
            print(f"  FAIL  create+assign  assigned={n} read={len(recs)}")

        # listing is scoped: visible to the anon session, not to a different one
        mine = records.list_datasets(fresh, session_id=sess)
        other = records.list_datasets(fresh, session_id="someone-else")
        if any(d["id"] == ds["id"] for d in mine) and not any(d["id"] == ds["id"] for d in other):
            print(f"  PASS  principal-scoping  (mine={len(mine)}, other excludes it)")
        else:
            failures += 1
            print(f"  FAIL  principal-scoping  mine_has={any(d['id']==ds['id'] for d in mine)} "
                  f"other_has={any(d['id']==ds['id'] for d in other)}")

        # the dataset detail reports the right record count
        got = records.get_dataset(fresh, ds["id"])
        if got and got["schema_id"] == "forestplot@v1" and got["session_id"] == sess:
            print("  PASS  get_dataset  (schema + owner session correct)")
        else:
            failures += 1
            print(f"  FAIL  get_dataset  got={got}")

        # a dataset can carry its extraction recipe (prompt + model) as a default
        rds = records.create_dataset(fresh, title="Recipe carrier", session_id=sess,
                                     schema_id="forestplot@v1", prompt="do the thing",
                                     model="openai:gpt-x")
        got_r = records.get_dataset(fresh, rds["id"])
        if (rds.get("prompt") == "do the thing" and rds.get("model") == "openai:gpt-x"
                and got_r["prompt"] == "do the thing" and got_r["model"] == "openai:gpt-x"):
            print("  PASS  recipe on dataset  (create + read prompt/model)")
        else:
            failures += 1
            print(f"  FAIL  recipe on dataset  create={rds.get('prompt')},{rds.get('model')} read={got_r.get('prompt')},{got_r.get('model')}")
    finally:
        fresh.close()

    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


def test_datasets() -> None:
    if not _db_ok():
        import pytest
        pytest.skip("no Postgres available")
    assert run() == 0


if __name__ == "__main__":
    raise SystemExit(run())
