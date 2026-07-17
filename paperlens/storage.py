"""Object storage for durable artifacts — uploaded PDFs and rendered page images
(plan §5 / brief §5: "in-memory page images -> object storage so any worker serves
them and they survive restarts").

A tiny backend-agnostic interface with two implementations:
  * LocalObjectStore — filesystem (default; zero-config dev, no boto3 needed),
  * S3ObjectStore    — S3 / Cloudflare R2 (boto3, lazy-imported), for prod.

``get_store()`` picks the backend from env so call sites never branch:
    PAPERLENS_STORAGE = local | s3        (default: local)
    PAPERLENS_STORAGE_ROOT = <dir>        (local)
    PAPERLENS_S3_BUCKET / _ENDPOINT / _PUBLIC_URL / AWS creds  (s3 / R2)

Keys are slash-namespaced, e.g. ``pdf/<doc_id>.pdf`` or ``pages/<doc_id>/3.jpg``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def url(self, key: str) -> str: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...


class LocalObjectStore:
    """Filesystem-backed store. ``url`` returns a path under a served prefix so the
    API can hand it back to the browser the same way the S3 backend hands back a URL.
    """

    def __init__(self, root: str | None = None, url_prefix: str = "/artifacts") -> None:
        self.root = Path(root or os.environ.get(
            "PAPERLENS_STORAGE_ROOT",
            os.path.join(os.path.dirname(__file__), "..", ".artifacts"))).resolve()
        self.url_prefix = url_prefix.rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # guard against traversal; keys are relative slash-paths
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root)):
            raise ValueError(f"key escapes storage root: {key!r}")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return self.url(key)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def url(self, key: str) -> str:
        return f"{self.url_prefix}/{key}"

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        import shutil
        shutil.rmtree(self._path(prefix), ignore_errors=True)


class S3ObjectStore:
    """S3 / Cloudflare R2 backed store. boto3 is imported lazily so the local
    backend (and its tests) never require it."""

    def __init__(self, bucket: str | None = None, *, endpoint_url: str | None = None,
                 public_url: str | None = None, region: str | None = None) -> None:
        import boto3  # lazy
        self.bucket = bucket or os.environ["PAPERLENS_S3_BUCKET"]
        self.public_url = (public_url or os.environ.get("PAPERLENS_S3_PUBLIC_URL", "")).rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or os.environ.get("PAPERLENS_S3_ENDPOINT") or None,
            region_name=region or os.environ.get("AWS_REGION"),
        )

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data,
                                ContentType=content_type, ServerSideEncryption="AES256")
        return self.url(key)

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def url(self, key: str) -> str:
        if self.public_url:
            return f"{self.public_url}/{key}"
        # Short-TTL presign: the owner fetches pages right after document_view (the
        # mint-time gate), so 10 min is ample and shrinks the leak window.
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=600)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self._client.get_paginator("list_objects_v2")
        batch: list[dict] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                batch.append({"Key": obj["Key"]})
                if len(batch) == 1000:
                    self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})
                    batch = []
        if batch:
            self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": batch})


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    """Process-wide store, chosen by env (default: local filesystem)."""
    global _store
    if _store is None:
        backend = os.environ.get("PAPERLENS_STORAGE", "local").lower()
        _store = S3ObjectStore() if backend == "s3" else LocalObjectStore()
    return _store


# key helpers — one place that knows the artifact layout
def pdf_key(doc_id: str) -> str:
    return f"pdf/{doc_id}.pdf"


def page_image_key(doc_id: str, page: int) -> str:
    return f"pages/{doc_id}/{page}.jpg"
