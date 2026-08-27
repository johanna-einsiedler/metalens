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


def _list(client, bucket: str, **kw) -> list[tuple[str, int]]:
    """ListObjectsV2 pages flattened to (key, size)."""
    out = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, **kw):
        out += [(o["Key"], o["Size"]) for o in page.get("Contents", [])]
    return out


def _iter_keys(store) -> list[tuple[str, int]]:
    """Every (key, size) under the two document-scoped prefixes, for either backend."""
    client = getattr(store, "_client", None)
    if client is not None:                                   # S3-compatible
        try:
            out = []
            for prefix in ("pdf/", "pages/"):
                out += _list(client, store.bucket, Prefix=prefix)
            return out
        except Exception as exc:                             # noqa: BLE001
            # Not every S3-compatible implementation handles a bare Prefix the way AWS
            # does (Supabase Storage answers NoSuchKey rather than an empty listing).
            # Fall back to walking the whole bucket and filtering here.
            print(f"prefix listing failed ({type(exc).__name__}: {exc})\n"
                  f"→ retrying with a full-bucket listing\n")
            return _list(client, store.bucket)
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


def _diagnose(conn, store) -> int:
    """Probe the S3 operations delete_document needs, one at a time, and say which work.

    ``delete_document`` needs exactly three: DeleteObject for the PDF, then ListObjectsV2
    + DeleteObjects for the page images. Uploads only need PutObject, so a backend can
    look perfectly healthy right up until someone deletes a paper.
    """
    client = store._client
    print(f"bucket:   {store.bucket}")
    print(f"endpoint: {os.environ.get('PAPERLENS_S3_ENDPOINT') or '(AWS default)'}")
    print(f"region:   {os.environ.get('AWS_REGION') or '(unset)'}\n")

    # A key we know exists, to tell "no permission" apart from "nothing there".
    row = conn.execute("SELECT id FROM extraction_document ORDER BY created_at DESC "
                       "LIMIT 1").fetchone()
    known = storage.pdf_key(str(row[0])) if row else None

    def probe(label: str, fn) -> None:
        try:
            print(f"  {'OK  ':<5} {label:<34} {fn()}")
        except Exception as exc:                             # noqa: BLE001
            print(f"  {'FAIL':<5} {label:<34} {type(exc).__name__}: {exc}")

    print("operation probes:")
    def _then(fn, msg):
        def run():
            fn()
            return msg
        return run

    probe("HeadBucket", _then(lambda: client.head_bucket(Bucket=store.bucket), "reachable"))
    probe("ListObjectsV2 (no prefix)",
          lambda: f"{len(_list(client, store.bucket))} key(s)")
    probe("ListObjectsV2 Prefix=pdf/",
          lambda: f"{len(_list(client, store.bucket, Prefix='pdf/'))} key(s)")
    probe("ListObjectsV2 Prefix+Delimiter",
          lambda: f"{len(_list(client, store.bucket, Prefix='pdf/', Delimiter='/'))} key(s)")
    if known:
        probe("HeadObject (a live PDF)",
              lambda: f"{client.head_object(Bucket=store.bucket, Key=known)['ContentLength']} bytes")
    # Deletes are idempotent in S3 — removing a key that was never there changes nothing,
    # so this probes the permission without touching a single real object.
    probe("DeleteObject (nonexistent key)",
          _then(lambda: client.delete_object(Bucket=store.bucket,
                                             Key="pdf/_probe_does_not_exist.pdf"), "allowed"))
    probe("DeleteObjects (nonexistent key)",
          _then(lambda: client.delete_objects(
              Bucket=store.bucket,
              Delete={"Objects": [{"Key": "pdf/_probe_does_not_exist.pdf"}]}), "allowed"))
    print("\ndelete_document needs DeleteObject + ListObjectsV2 + DeleteObjects.\n"
          "Whichever of those says FAIL is what was turning a paper delete into a 500.")
    return 0


def _try_endpoint(store, endpoint: str) -> int:
    """List the bucket through a DIFFERENT endpoint URL, changing nothing.

    A bucket name left on the end of PAPERLENS_S3_ENDPOINT is invisible to per-key
    operations — puts and gets both go through the same doubled path, so they agree with
    each other — but it breaks every bucket-level call. Correcting it silently moves where
    the app looks for keys, so confirm the real layout here FIRST: whatever prefix shows up
    below is where the existing objects actually live.
    """
    import boto3
    client = boto3.client("s3", endpoint_url=endpoint,
                          region_name=os.environ.get("AWS_REGION"))
    print(f"probing:  {endpoint}")
    print(f"bucket:   {store.bucket}   (read-only — this deletes and writes nothing)\n")
    try:
        keys = _list(client, store.bucket)
    except Exception as exc:                                 # noqa: BLE001
        print(f"  FAIL  ListObjectsV2  {type(exc).__name__}: {exc}")
        return 1

    print(f"  OK    ListObjectsV2  {len(keys)} key(s)\n")
    if not keys:
        print("The bucket lists clean but is empty — wrong bucket, or the objects live "
              "elsewhere.")
        return 0
    tops: dict[str, int] = defaultdict(int)
    for key, _size in keys:
        tops[key.split("/", 1)[0] if "/" in key else "(root)"] += 1
    print("top-level prefixes:")
    for top, n in sorted(tops.items(), key=lambda kv: -kv[1]):
        print(f"  {top + '/':<24} {n:>6} key(s)")
    print("\nsample keys:")
    for key, size in keys[:5]:
        print(f"  {key}  ({size} bytes)")
    if set(tops) <= {"pdf", "pages", "text"}:
        print("\nThis is the layout the app expects — switching to this endpoint is safe.")
    else:
        print(f"\nThe app writes pdf/, pages/ and text/ at the ROOT. What's here is nested "
              f"under {sorted(tops)}, so switching endpoints would hide every existing\n"
              f"object until the keys are moved up a level.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--identify", action="store_true",
                    help="download each orphaned PDF to name the paper (1 GET per orphan)")
    ap.add_argument("--diagnose", action="store_true",
                    help="probe each S3 operation and report which ones the backend supports")
    ap.add_argument("--try-endpoint", metavar="URL",
                    help="read-only: re-probe against a different endpoint URL and show the "
                         "key layout it sees (use before changing PAPERLENS_S3_ENDPOINT)")
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

    if args.try_endpoint:
        if not s3:
            print("--try-endpoint probes S3 operations; this is the filesystem store.")
            return 0
        return _try_endpoint(store, args.try_endpoint)

    if args.diagnose:
        if not s3:
            print("--diagnose probes S3 operations; this is the filesystem store.")
            return 0
        return _diagnose(conn, store)

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
