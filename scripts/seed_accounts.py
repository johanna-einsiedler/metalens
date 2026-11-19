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

A manifest with ``workflow`` seeds a PROJECT instead — the workflow's instance: one owner, the
people added by email, one dataset per role of the workflow (paperlens/projects.py,
docs/WORKFLOWS.md):

    {"base": "https://metalens.tech", "workflow": "genai-work", "title": "GenAI at work — validation",
     "owner": {"email": "owner@example.org", "password": "…"},
     "coders": ["coder-a@example.org", {"email": "coder-b@example.org", "password": "…"}],
     "produced_by": "pipeline v4",
     "papers": {"extraction": "../import_pilot"},
     "only": {"extraction": ["0ae57f3a2520", "1b2c3d4e5f60"]},
     "tasks": {"review_judgment": {"coders": 1}}}

As the owner (registered or signed in): find the project by title or make it, link a dataset for
every role it lacks, add each person (an account that exists; given with a password, it is
registered first), set each task's people where ``tasks`` says so (how many of the project's
people review each paper, or which — their emails), start a run (or use the active one), upload
each side's pairs that its dataset lacks into the run with the step, then mark each side listed
under ``papers`` complete and say what the review tasks wait for. The coders' passwords never
travel: a coder registers on their own and is added by email.
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


# ── a workflow: one owner, coders by email, one workflow dataset, a run fed side by side ──

def _workflow_panel(c: httpx.Client, wd: str) -> dict:
    r = c.get(f"/api/datasets/{wd}/workflow")
    if r.status_code != 200:
        _die(f"workflow panel of {wd}: {r.status_code} {r.text[:200]}")
    return r.json()


def seed_workflow(c: httpx.Client, m: dict, root: Path, dry: bool, *, client_factory=None) -> dict:
    """The owner path (see the module docstring). ``client_factory`` makes a fresh client for
    registering a coder given with a password, so the owner's session stays the owner's."""
    wf, title = m["workflow"], m.get("title") or m["workflow"]
    owner = m.get("owner") or {}
    for k in ("email", "password"):
        if not owner.get(k):
            _die(f"the owner is missing `{k}`")
    only = m.get("only") or {}
    todo = {role: pairs(root / folder, only.get(role), root) for role, folder in (m.get("papers") or {}).items()}
    coders = [{"email": x} if isinstance(x, str) else dict(x) for x in (m.get("coders") or [])]
    tasks = m.get("tasks") or {}
    if dry:
        print(f"\n{owner['email']} → project “{title}” of workflow “{wf}”")
        print(f"  would add {len(coders)} person(s): {', '.join(x['email'] for x in coders) or '—'}")
        for step_id, cfg in tasks.items():
            print(f"  task {step_id}: would set coders = {json.dumps((cfg or {}).get('coders'))}")
        for role, items in todo.items():
            print(f"  {role}: would upload {len(items)} paper(s)")
        return {"dry": True}
    # the coders' accounts first (their own sessions, never the owner's)
    for x in coders:
        if x.get("password"):
            with (client_factory() if client_factory else httpx.Client(base_url=c.base_url, timeout=c.timeout)) as cc:
                print(f"  {sign_in(cc, x['email'], x['password'])}: {x['email']}")
                cc.post("/api/auth/logout")
    print(f"\n{owner['email']}")
    print(f"  {sign_in(c, owner['email'], owner['password'])}")
    # the project, by title
    mine = c.get("/api/projects").json().get("projects") or []
    wf_of = lambda w: (w.get("workflow") or {}).get("id") if isinstance(w.get("workflow"), dict) else str(w.get("workflow") or "").split("@")[0]   # noqa: E731
    proj = next((w for w in mine if w.get("title") == title and (not wf_of(w) or wf_of(w) == wf)), None)
    if proj is None:
        r = c.post("/api/projects", json={"workflow": wf, "title": title})
        if r.status_code != 200:
            _die(f"could not make the project: {r.status_code} {r.text[:200]}")
        proj = r.json(); how = "made"
    else:
        how = "found"
    wid = proj["id"]
    print(f"  {how}: {c.base_url}/project?id={wid}")
    # one dataset per role
    page = c.get(f"/api/projects/{wid}").json()
    if page.get("kind") != "project":
        _die(f"“{wf}” runs over one preset, so “{title}” is a plain dataset, not a project; seed it in the account mode")
    for item in page.get("members") or []:
        if not item.get("dataset"):
            r = c.post(f"/api/datasets/{wid}/group/link", json={"role": item["role"], "create": True})
            if r.status_code != 200:
                _die(f"could not link a dataset for {item['role']}: {r.status_code} {r.text[:200]}")
            print(f"  linked a new dataset for {item['role']}")
    # the people
    have = {str(x.get("email") or "").lower() for x in c.get(f"/api/projects/{wid}/people").json().get("people") or []}
    for x in coders:
        if x["email"].lower() in have:
            continue
        r = c.post(f"/api/projects/{wid}/people", json={"email": x["email"]})
        print(f"  {'person added' if r.status_code == 200 else 'person NOT added'}: {x['email']}{'' if r.status_code == 200 else ' — ' + r.text[:120]}")
    # the tasks' people, where the manifest says
    for step_id, cfg in tasks.items():
        want = (cfg or {}).get("coders")
        r = c.patch(f"/api/projects/{wid}/tasks/{step_id}", json={"coders": want})
        print(f"  task {step_id}: {'coders = ' + json.dumps(want) if r.status_code == 200 else 'NOT set — ' + r.text[:120]}")
    # the run and its sides
    panel = _workflow_panel(c, wid)
    run = panel.get("current_run")
    if not run or run.get("status") in ("done", "failed", "cancelled"):
        r = c.post(f"/api/datasets/{wid}/workflow/runs", json={})
        if r.status_code != 200:
            _die(f"could not start a run: {r.status_code} {r.text[:200]}")
        run = r.json(); print(f"  run started: {run['id']}")
    else:
        print(f"  run in progress: {run['id']} ({run.get('status')})")
    sides = {s["role"]: s for s in panel.get("steps") or [] if s.get("kind") == "extract" and s.get("role")}
    summary = {}
    for role, items in todo.items():
        side = sides.get(role)
        if side is None or not side.get("dataset_id"):
            _die(f"the workflow has no linked side {role!r}")
        have = {d.get("filename") for d in c.get("/api/documents", params={"dataset": side["dataset_id"], "limit": 5000}).json()["documents"]}
        done = skipped = failed = 0
        for paper_id, js, pdf in items:
            if pdf.name in have:
                skipped += 1; continue
            data = {"result": js.read_text(), "run_id": run["id"], "step_id": side["id"]}
            if m.get("produced_by"):
                data["produced_by"] = m["produced_by"]
            r = c.post("/api/ingest-pdf", data=data, files={"pdf": (pdf.name, pdf.read_bytes(), "application/pdf")})
            if r.status_code != 200:
                failed += 1; print(f"  ✗ {role} {paper_id}: {r.status_code} {r.text[:160]}"); continue
            done += 1
            j = r.json()
            print(f"  ✓ {role} {paper_id}: {j.get('n_records')} record(s), {j.get('n_highlights')} quote(s) located, {j.get('n_pages')} pages")
        r = c.post(f"/api/workflow-runs/{run['id']}/complete", json={"step_id": side["id"]})
        print(f"  {role}: {done} imported · {skipped} already there · {failed} failed · side {'complete' if r.status_code == 200 else 'NOT completed: ' + r.text[:120]}")
        summary[role] = {"imported": done, "skipped": skipped, "failed": failed}
    view = c.get(f"/api/workflow-runs/{run['id']}").json()
    tasks_now = c.get(f"/api/projects/{wid}/tasks", params={"run": run["id"]}).json().get("tasks") or []
    gate = lambda t: t.get("gate") or {}   # noqa: E731
    print(f"  run {view.get('status')}; tasks: " + ("; ".join(
        f"{t['step_id']} {gate(t).get('checked', 0)}/{gate(t).get('total', 0)}" + (f" (waits for {gate(t)['needs_coders']} coders)" if gate(t).get("needs_coders") else "")
        for t in tasks_now) or "none yet"))
    c.post("/api/auth/logout")
    return {"project": wid, "workflow_dataset": wid, "run": run["id"], "sides": summary, "status": view.get("status"),
            "tasks": {t["step_id"]: gate(t).get("total") for t in tasks_now}}


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
    if m.get("workflow"):
        with httpx.Client(base_url=base, timeout=httpx.Timeout(1800.0, connect=30.0)) as c:
            seed_workflow(c, m, root, args.dry_run)
        return
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
