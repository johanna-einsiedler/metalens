"""Create coder accounts and seed each with a preset and a dataset of imported papers — over
HTTP, against any Metalens server (metalens.tech or a local one), from one manifest file.

    .venv/bin/python scripts/seed_accounts.py manifest.json [--base URL] [--dry-run]

manifest.json (paths are relative to the manifest's own folder):

    {"base": "https://metalens.tech",
     "preset": "search/screening/metalens_preset/genai-work-fulltext-t1.json",
     "papers": "search/fulltext/metalens/import",
     "produced_by": "gemini-3.5-flash · screening v5",
     "accounts": [
       {"email": "coder-a@example.org", "password": "…", "dataset": "Full-text screening — A"},
       {"email": "coder-b@example.org", "password": "…", "dataset": "Full-text screening — B",
        "papers": ["0ae57f3a2520", "1b2c3d4e5f60"]}]}

``papers`` is a folder of ``<id>.json`` import files with their ``<id>.pdf`` next to them. An
account's own ``papers`` is either a list of ids (a subset of that folder) or a folder of its own
(``"papers": "coder1/papers"``); without it every pair in the shared folder goes in.
Per account: register (or sign in when the account exists), upload the preset as that account's
own private copy, create the dataset under the preset's schema, import each pair into it with
its PDF so the quotes are located. Re-running is safe: an existing preset (same title) and
dataset (same title) are reused and papers already in the dataset are skipped. No model is
called and no credits are needed. Keep the manifest out of git — it holds passwords.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx


def _die(msg: str) -> None:
    print(f"! {msg}", file=sys.stderr)
    sys.exit(1)


def sign_in(c: httpx.Client, email: str, password: str) -> str:
    r = c.post("/api/auth/register", json={"email": email, "password": password})
    if r.status_code == 200:
        return "account created"
    if r.status_code != 409:
        _die(f"{email}: register failed — {r.status_code} {r.text[:200]}")
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        _die(f"{email}: the account exists and the manifest's password doesn't open it")
    return "signed in"


def own_preset(c: httpx.Client, spec: dict) -> tuple[str, str, str]:
    """The account's own copy of the preset: (schema id, preset id, what happened)."""
    title = spec["meta"]["title"]
    for row in c.get("/api/presets", params={"usage": "import"}).json()["presets"]:
        rtitle = ((row.get("spec") or {}).get("meta") or {}).get("title") or row.get("title")
        if row.get("personal") and row.get("owned") and rtitle == title:
            return row["schema_id"], row.get("preset_id") or row.get("id"), "preset reused"
    r = c.post("/api/presets", json={"spec": spec, "visibility": "private", "keep_id": True})
    if r.status_code != 200:
        _die(f"preset upload failed — {r.status_code} {r.text[:300]}")
    j = r.json()
    return j["schema_id"], j["id"], "preset uploaded"


def own_dataset(c: httpx.Client, title: str, schema_id: str) -> tuple[str, str]:
    for d in c.get("/api/datasets").json()["datasets"]:
        if d["title"] == title and d.get("schema_id") == schema_id:
            return d["id"], "dataset reused"
    r = c.post("/api/datasets", json={"title": title, "schema_id": schema_id, "visibility": "private"})
    if r.status_code != 200:
        _die(f"dataset create failed — {r.status_code} {r.text[:300]}")
    return r.json()["id"], "dataset created"


def pairs(folder: Path | None, own, root: Path) -> list[tuple[str, Path, Path]]:
    """The account's (id, json, pdf) triples: ``own`` is a list of ids in the shared ``folder``, or
    the account's own folder, or None for every pair in the shared folder."""
    ids = own if isinstance(own, list) else None
    if isinstance(own, str):
        folder = root / own
    if folder is None or not folder.is_dir():
        _die(f"{folder or 'papers'} is not a folder")
    out = []
    for pid in ids or sorted(p.stem for p in folder.glob("*.json")):
        js, pdf = folder / f"{pid}.json", folder / f"{pid}.pdf"
        if not js.exists():
            print(f"  ! {pid}: no {pid}.json — skipped")
        elif not pdf.exists():
            print(f"  ! {pid}: no {pid}.pdf next to it — skipped (the PDF is what locates the quotes)")
        else:
            out.append((pid, js, pdf))
    return out


def seed(c: httpx.Client, acct: dict, spec: dict, folder: Path | None, root: Path,
         produced_by: str | None, dry: bool) -> None:
    email = acct["email"]
    print(f"\n{email}")
    todo = pairs(folder, acct.get("papers"), root)
    if dry:
        print(f"  would sign in, own a copy of “{spec['meta']['title']}”, make dataset “{acct['dataset']}”, "
              f"import {len(todo)} paper(s)")
        return
    print(f"  {sign_in(c, email, acct['password'])}")
    schema_id, pid, how = own_preset(c, spec)
    print(f"  {how}: {pid} ({schema_id})")
    ds, how = own_dataset(c, acct["dataset"], schema_id)
    print(f"  {how}: {c.base_url}/dataset?id={ds}")
    have = {d.get("filename") for d in c.get("/api/documents", params={"dataset": ds, "limit": 5000}).json()["documents"]}
    done = skipped = failed = 0
    for paper_id, js, pdf in todo:
        if pdf.name in have:
            skipped += 1
            continue
        data = {"result": js.read_text(), "schema_id": schema_id, "dataset_id": ds}
        if produced_by:
            data["produced_by"] = produced_by
        r = c.post("/api/ingest-pdf", data=data, files={"pdf": (pdf.name, pdf.read_bytes(), "application/pdf")})
        if r.status_code != 200:
            failed += 1
            print(f"  ✗ {paper_id}: {r.status_code} {r.text[:160]}")
            continue
        j = r.json()
        done += 1
        print(f"  ✓ {paper_id}: {j.get('n_records')} record(s), {j.get('n_highlights')} quote(s) located, {j.get('n_pages')} pages")
    print(f"  {done} imported · {skipped} already there · {failed} failed")
    c.post("/api/auth/logout")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--base", help="server URL (overrides the manifest's `base`)")
    ap.add_argument("--dry-run", action="store_true", help="list what would happen; touch nothing")
    args = ap.parse_args()
    m = json.loads(args.manifest.read_text())
    root = args.manifest.resolve().parent
    base = (args.base or m.get("base") or "").rstrip("/")
    if not base:
        _die("no server: set `base` in the manifest or pass --base")
    spec = json.loads((root / m["preset"]).read_text())
    if spec.get("format") != 2 or not (spec.get("meta") or {}).get("title"):
        _die(f"{m['preset']} is not a format-2 preset file")
    folder = root / m["papers"] if m.get("papers") else None
    for acct in m.get("accounts") or []:
        for k in ("email", "password", "dataset"):
            if not acct.get(k):
                _die(f"an account is missing `{k}`")
        with httpx.Client(base_url=base, timeout=httpx.Timeout(600.0, connect=30.0)) as c:
            seed(c, acct, spec, folder, root, m.get("produced_by"), args.dry_run)


if __name__ == "__main__":
    main()
