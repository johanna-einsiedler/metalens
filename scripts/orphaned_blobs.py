"""Find PDFs / page images in object storage with no document row — READ-ONLY.

Until the blob cleanup in ``records.delete_document`` was made best-effort, it ran
AFTER the DB delete had committed and raised straight out of the request. On the
S3/R2 backend (beta) that meant: the paper was really deleted, the reviewer saw a
500, and the blobs were left behind. The local backend can't fail, so this only
ever happened on the deployed app.

That makes the leftovers a forensic record. A delete that SUCCEEDED removed its
blobs; a delete that 500'd did not. So every orphan listed here is one paper that
was silently deleted while its owner was told the delete had failed.

The script only ever lists and reads. It deletes nothing and writes nothing.

Usage (on the beta machine, where the bucket creds + DB are in the env):
    fly ssh console -C "python scripts/orphaned_blobs.py"
    fly ssh console -C "python scripts/orphaned_blobs.py --identify"   # name the papers

Locally against the dev DB (PAPERLENS_STORAGE defaults to the filesystem store):
    uv run python scripts/orphaned_blobs.py

``--identify`` costs one GET per orphan: it downloads the orphaned PDF to hash it,
then names the paper from a surviving extraction of the same file, falling back to
the cached parse's first heading.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import records, storage  # noqa: E402

# pdf/<uuid>.pdf  and  pages/<uuid>/<n>.jpg — the two layouts delete_document cleans up.
_KEY_RE = re.compile(
    r"^(?:pdf/(?P<a>[0-9a-f-]{36})\.pdf|pages/(?P<b>[0-9a-f-]{36})/.*)$", re.I)


def _iter_keys(store) -> list[tuple[str, int]]:
    """Every (key, size) under the two document-scoped prefixes, for either backend."""
    client = getattr(store, "_client", None)
    if client is not None:                                   # S3 / R2
        out = []
        paginator = client.get_paginator("list_objects_v2")
        for prefix in ("pdf/", "pages/"):
            for page in paginator.paginate(Bucket=store.bucket, Prefix=prefix):
                out += [(o["Key"], o["Size"]) for o in page.get("Contents", [])]
        return out
    root = store.root                                        # LocalObjectStore
    out = []
    for prefix in ("pdf", "pages"):
        for dirpath, _dirs, files in os.walk(root / prefix):
            for fn in files:
                full = os.path.join(dirpath, fn)
                out.append((os.path.relpath(full, root), os.path.getsize(full)))
    return out


def _identify(conn, store, doc_id: str) -> str:
    """Best-effort name for a deleted document, from its surviving PDF blob."""
    try:
        sha = hashlib.sha256(store.get(storage.pdf_key(doc_id))).hexdigest()
    except Exception as exc:                                 # noqa: BLE001
        return f"(couldn't read the PDF: {exc})"
    # Best signal: the same file was extracted again and that document still exists.
    row = conn.execute(
        """SELECT p.title, d.filename FROM extraction_document d
           LEFT JOIN paper p ON p.id = d.paper_id
           WHERE d.pdf_sha256 = %s ORDER BY d.created_at DESC LIMIT 1""", (sha,)).fetchone()
    if row and (row[0] or row[1]):
        return f"{row[0] or row[1]}  (re-extracted since — still in the library)"
    # Otherwise the cached parse survives (keyed by hash); its first heading will do.
    row = conn.execute(
        "SELECT md_key, n_pages FROM parsed_document WHERE pdf_sha256 = %s", (sha,)).fetchone()
    if row and row[0]:
        try:
            head = store.get(row[0]).decode("utf-8", "replace")[:4000]
            # skip the "--- PDF page N of M ---" separators the parser emits
            line = next((l.strip(" #") for l in head.splitlines()
                         if l.strip() and not l.startswith("--- PDF page")), "")
            if line:
                return f"{line[:100]}  ({row[1]} pages, from the cached parse)"
        except Exception:                                    # noqa: BLE001
            pass
    return f"(unidentified; sha256 {sha[:12]}…)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--identify", action="store_true",
                    help="download each orphaned PDF to name the paper (1 GET per orphan)")
    args = ap.parse_args()

    conn = records.connect()
    store = storage.get_store()
    s3 = getattr(store, "_client", None) is not None
    print(f"store:    {type(store).__name__}")
    print(f"database: {records.dsn().split('@')[-1]}\n")
    if not s3:
        print("NOTE: this is the local filesystem store, which cannot fail a delete — so\n"
              "      orphans here are ordinary dev debris (wiped DBs, purged test rows),\n"
              "      NOT the 500 bug. Only an S3/R2 run answers that question.\n")

    blobs: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for key, size in _iter_keys(store):
        m = _KEY_RE.match(key.replace(os.sep, "/"))
        if m:
            blobs[(m.group("a") or m.group("b")).lower()].append((key, size))
    if not blobs:
        print("No document-scoped blobs in storage at all.")
        return 0

    live = {str(r[0]) for r in conn.execute(
        "SELECT id FROM extraction_document WHERE id::text = ANY(%s)",
        (list(blobs),)).fetchall()}
    orphans = sorted(set(blobs) - live)

    print(f"{len(blobs)} document(s) with blobs · {len(live)} live · {len(orphans)} orphaned")
    if not orphans:
        print("\nNothing orphaned — every stored blob belongs to a document that still exists.")
        return 0

    total = 0
    print("\nOrphaned — blobs whose document row is gone"
          + (" (on this backend, each one is a delete that 500'd):\n" if s3
             else " (dev debris; see the note above):\n"))
    for doc_id in orphans:
        files = blobs[doc_id]
        nbytes = sum(s for _k, s in files)
        total += nbytes
        print(f"  {doc_id}  {len(files):>4} file(s)  {nbytes / 1e6:>7.1f} MB")
        if args.identify:
            print(f"      ↳ {_identify(conn, store, doc_id)}")
    print(f"\n{len(orphans)} document(s), {total / 1e6:.1f} MB still in storage.")
    if s3:
        print("The PDFs and page images survive; their records, evidence, and verification\n"
              "history do not — those cascaded away with the document row.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
