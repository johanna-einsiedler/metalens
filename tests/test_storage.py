"""Object-store tests — LocalObjectStore round-trip + traversal guard.

Offline, no Redis/boto3 needed (the S3 backend lazy-imports boto3).
"""
from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import storage  # noqa: E402


def test_local_store_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d, url_prefix="/artifacts")
        key = storage.page_image_key("doc-123", 3)
        assert key == "pages/doc-123/3.jpg"

        url = store.put(key, b"\xff\xd8jpegbytes", content_type="image/jpeg")
        assert url == "/artifacts/pages/doc-123/3.jpg"
        assert store.exists(key)
        assert store.get(key) == b"\xff\xd8jpegbytes"

        store.delete(key)
        assert not store.exists(key)


def test_local_store_blocks_traversal() -> None:
    with tempfile.TemporaryDirectory() as d:
        store = storage.LocalObjectStore(root=d)
        try:
            store.put("../escape.txt", b"nope")
            raised = False
        except ValueError:
            raised = True
        assert raised, "expected traversal to be rejected"


def test_get_store_defaults_to_local(monkeypatch=None) -> None:
    os.environ.pop("PAPERLENS_STORAGE", None)
    storage._store = None  # reset the process-wide singleton
    assert isinstance(storage.get_store(), storage.LocalObjectStore)


def _main() -> int:
    failures = 0
    for label, fn in [
        ("storage:roundtrip", test_local_store_roundtrip),
        ("storage:traversal-guard", test_local_store_blocks_traversal),
        ("storage:default-local", test_get_store_defaults_to_local),
    ]:
        try:
            fn()
            print(f"  PASS  {label}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {label}: {exc!r}")
    print(f"\n{'OK' if not failures else 'FAILURES: ' + str(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
