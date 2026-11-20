#!/usr/bin/env python3
"""Seed a Metalens dataset running the ``genai-work-chain`` workflow from the review project's
files: for each paper the PDF with the project's own page text and identity, then the six pass
outputs the project extracted locally (``labeling/split/<rid>/`` exhibits, estimands, estimates
and design — with the readings' annotations and settled cells lifted from the record and the
stability record beside them — the claims, and the headline plan), the second and third readings
as run outputs when present. The chain's compute steps then assemble, check and route each paper
inside Metalens exactly as they would after live passes.

    .venv/bin/python scripts/seed_genai_chain.py --project /path/to/49_AI_review --rid RID [--rid …] | --ids FILE
        [--url http://127.0.0.1:8775] [--title "GenAI chain pilot"] [--pdfs DIR] [--no-runs] [--dry]

Order matters: the PDF is staged first (``dispatch=false``, no model call), because the chain
advances as soon as the last pass output of a staged paper is supplied and the assemble step
needs the PDF. The project is read only.

With ``--manifest`` the seeder works as the v4 seeder's project mode does (``seed_accounts.py``:
the same manifest file, kept out of git): the owner signs in, the PROJECT of the workflow is found
by title or made, a dataset linked for the role it lacks, the people added by email, each task's
coders set, and the papers of ``only`` (or ``--rid``) staged and supplied into a run; the review
tasks then assign the project's people. ``--workflow`` and ``--title`` override the manifest's
(so the v4 pilot's manifest seeds the chain's project), ``local: true`` / ``--local`` serves an
engine in local mode (no accounts).

    .venv/bin/python scripts/seed_genai_chain.py --manifest /path/to/49_AI_review/labeling/metalens/seed/seed_workflow.json \
        --workflow genai-work-chain --title "GenAI at work — the chain" [--rid …] [--base URL] [--stale-plans] [--dry]
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from paperlens.chains.genai_work import text as X  # noqa: E402

WORKFLOW = "genai-work-chain"
PRESET = "genai-work-extraction"
STEP_ORDER = ("exhibits", "estimands", "estimates", "design", "claims", "headline_plan")
DESIGN_HASH_OF_SCHEMA = {"b5ed1ca67c": "afbb8492d2", "9c7787dd92": "c10c2460c9"}   # the design prompt's hash under each taxonomy schema the project ran
CLAIMS_KEYS = ("status", "abstract", "intro", "sentences", "intro_sentences", "estimates_claimed", "main_unclaimed", "counts")
PLAN_KEYS = ("units", "task_evidence", "appendix", "atomic_claims", "conclusion_sentences", "uncovered_units", "numeric_claims", "chains", "decisions")


# ── the project's files ─────────────────────────────────────────────────────────────────────

def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def pool_entry(project: Path, rid: str) -> dict:
    f = project / "data" / "abstracts" / "pool.jsonl"
    if not f.exists():
        return {}
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("record_id") == rid:
            return e
    return {}


def paper_meta(project: Path, rid: str) -> dict:
    """What the passes and the document know of the paper: title, doi, year, authors, journal and
    peer-review status as the project's own import file states them (``import_pilot/<rid>.json``),
    else from the pool; the abstract (the claims pass anchors the PDF's abstract on it) from the pool."""
    e = pool_entry(project, rid)
    rec = _json(project / "labeling" / "split" / rid / "record.json") or {}
    imp = (_json(project / "labeling" / "metalens" / "import_pilot" / f"{rid}.json") or {}).get("paper_metadata") or {}
    year = imp.get("year")
    if year is None and (e.get("date") or "")[:4].isdigit():
        year = int(e["date"][:4])
    return {"title": imp.get("title") or e.get("title") or rec.get("title") or "", "doi": imp.get("doi") or e.get("doi"), "abstract": e.get("abstract") or "",
            "journal": imp.get("journal") if imp else e.get("venue"), "year": year, "authors": imp.get("authors") or e.get("authors") or [],
            "peer_reviewed": imp.get("peer_reviewed")}


def pages_for(project: Path, rid: str) -> list[str]:
    """The project's page text, one string per page (``data/pdfs/txt/<rid>.txt`` with its page markers)."""
    raw = (project / "data" / "pdfs" / "txt" / f"{rid}.txt").read_text(encoding="utf-8", errors="ignore")
    return X.Pages.from_raw(raw).list


def _vid(x):
    return x.get("value") if isinstance(x, dict) and "value" in x else x


def lift_runs(rec: dict, es: dict, em: dict, de: dict) -> tuple[dict, dict, dict]:
    """The readings' annotations the project wrote on the record (``stability.annotate``: ``_runs`` on
    the study, tasks, arms and rows; settled design cells) carried back onto the pass outputs, so
    the chain's assembly paints them as the matchers would have."""
    es, em, de = copy.deepcopy(es), copy.deepcopy(em), copy.deepcopy(de)
    study, dstudy = rec.get("study") or {}, de.setdefault("study", {})
    if study.get("_runs"):
        dstudy["_runs"] = study["_runs"]
    for f, c in study.items():
        if isinstance(c, dict) and c.get("settled_by") and f in dstudy:
            cell = dstudy[f] if isinstance(dstudy[f], dict) else {"value": dstudy[f]}
            cell.update(value=c.get("value"), settled_by=c["settled_by"]); dstudy[f] = cell
    d_tasks = {t.get("task_id"): t for t in de.get("tasks") or []}
    e_tasks = {t.get("task_id"): t for t in es.get("tasks") or []}
    for t in rec.get("tasks") or []:
        tid = _vid(t.get("task_id"))
        dt, et = d_tasks.get(tid), e_tasks.get(tid)
        if t.get("_runs"):
            if dt is not None:
                dt["_runs"] = t["_runs"]
            if et is not None:
                et["_runs"] = {k: v for k, v in t["_runs"].items() if k != "alt"} | {"alt": {}}
        if dt is not None:
            for f, c in t.items():
                if isinstance(c, dict) and c.get("settled_by") and f in dt:
                    cell = dt[f] if isinstance(dt[f], dict) else {"value": dt[f]}
                    cell.update(value=c.get("value"), settled_by=c["settled_by"]); dt[f] = cell
            d_arms = {a.get("arm_id"): a for a in dt.get("arms") or []}
            for a in t.get("arms") or []:
                da = d_arms.get(_vid(a.get("arm_id")))
                if da is None:
                    continue
                if a.get("_runs"):
                    da["_runs"] = a["_runs"]
                for f, c in a.items():
                    if isinstance(c, dict) and c.get("settled_by") and f in da:
                        cell = da[f] if isinstance(da[f], dict) else {"value": da[f]}
                        cell.update(value=c.get("value"), settled_by=c["settled_by"]); da[f] = cell
    ests = {x.get("estimate_id"): x for x in em.get("estimates") or []}
    for t in rec.get("tasks") or []:
        for r in t.get("results") or []:
            if _vid(r.get("vs_arm")) and r.get("_runs") and (r.get("estimate_id") or r.get("estimate")) in ests:
                ests[r.get("estimate_id") or r.get("estimate")]["_runs"] = r["_runs"]
    return es, em, de


def supplies(project: Path, rid: str, *, with_runs: bool = True, stale_plan: bool = False) -> list[dict]:
    """The pass outputs to supply for one paper, in the order they are sent: the runs (r2, r3)
    first, then the primaries in chain order, each ``{step, role, pass_index, output, prompt_sha256, stability}``."""
    d = project / "labeling" / "split" / rid
    ex, es, em, de = (_json(d / f"{n}.json") for n in ("exhibits", "estimands", "estimates", "design"))
    rec_file = _json(d / "record.json") or {}
    if not all((ex, es, em, de)):
        raise FileNotFoundError(f"{rid}: the four pass files are not all under {d}")
    es, em, de = lift_runs(rec_file.get("record") or {}, es, em, de)
    stab = _json(d / "stability.json") or {}
    snap = _json(HERE.parent / "paperlens" / "chains" / "genai_work" / "taxonomy.json") or {}
    split_hash = rec_file.get("prompt_hash")
    design_hash = DESIGN_HASH_OF_SCHEMA.get(rec_file.get("schema_hash")) or (snap.get("hashes") or {}).get("design")
    out: list[dict] = []
    if with_runs:
        for i, tag in enumerate(("r2", "r3"), 1):
            rd = d / "runs" / tag
            if not rd.is_dir():
                continue
            for step in ("exhibits", "estimands", "estimates", "design"):
                o = _json(rd / f"{step}.json")
                if o is not None:
                    out.append({"step": step, "role": "run", "pass_index": i, "output": o, "prompt_sha256": design_hash if step == "design" else split_hash, "stability": None})
    stab_of = {"estimands": {k: stab[k] for k in ("alignment", "metrics", "runs") if k in stab} or None,
               "estimates": {k: stab[k] for k in ("estimates", "runs") if k in stab} or None,
               "design": {k: stab[k] for k in ("design", "design_majority", "runs") if k in stab} or None}
    for step, o in (("exhibits", ex), ("estimands", es), ("estimates", em), ("design", de)):
        out.append({"step": step, "role": "primary", "pass_index": 0, "output": o, "prompt_sha256": design_hash if step == "design" else split_hash,
                    "stability": stab_of.get(step)})
    claims = _json(d / "claims.json")
    if claims:
        out.append({"step": "claims", "role": "primary", "pass_index": 0, "output": {k: claims.get(k) for k in CLAIMS_KEYS}, "prompt_sha256": claims.get("hash"), "stability": None})
    else:
        out.append({"step": "claims", "role": "primary", "pass_index": 0, "output": {"status": "not_run", "sentences": [], "intro_sentences": [], "estimates_claimed": {}, "main_unclaimed": [], "counts": {}},
                    "prompt_sha256": None, "stability": None})
    plan = _json(project / "labeling" / "headline_plan" / f"trial_{rid}.json")
    if plan and (stale_plan or [e.get("estimand_id") for e in plan.get("estimands") or []] == [e.get("estimand_id") for e in es.get("estimands") or []]):
        out.append({"step": "headline_plan", "role": "primary", "pass_index": 0, "output": {k: plan.get(k) for k in PLAN_KEYS}, "prompt_sha256": None, "stability": None})
    else:
        out.append({"step": "headline_plan", "role": "primary", "pass_index": 0, "output": {"units": [], "task_evidence": {}, "appendix": None, "atomic_claims": [], "conclusion_sentences": [],
                                                                                         "uncovered_units": [], "numeric_claims": [], "chains": [], "decisions": [], "stale": bool(plan)},
                    "prompt_sha256": None, "stability": None})
    return out


def bundle_for(project: Path, rid: str, *, with_runs: bool = True, stale_plan: bool = False) -> dict:
    """One paper's results as the project page's "Upload papers with results" takes them: the page
    text, the identity, the primaries by step (``outputs``) with their prompt hashes and stability
    records, and the further readings (``runs``). Saved beside the PDF as ``<rid>.json``."""
    sup = supplies(project, rid, with_runs=with_runs, stale_plan=stale_plan)
    meta = paper_meta(project, rid)
    out = {"pages": pages_for(project, rid), **{k: meta.get(k) for k in ("title", "doi", "abstract", "journal", "year", "authors", "peer_reviewed")},
           "outputs": {}, "prompt_sha256": {}, "stability": {}, "runs": []}
    for s in sup:
        if s["role"] == "primary":
            out["outputs"][s["step"]] = s["output"]
            if s.get("prompt_sha256"):
                out["prompt_sha256"][s["step"]] = s["prompt_sha256"]
            if s.get("stability"):
                out["stability"][s["step"]] = s["stability"]
        else:
            out["runs"].append({"step": s["step"], "pass_index": s["pass_index"], "output": s["output"], "prompt_sha256": s.get("prompt_sha256")})
    return out


def write_bundles(project: Path, rids: list[str], out_dir: Path, pdf_dir: Path, *, with_runs: bool = True, stale_plan: bool = False) -> list[str]:
    """``<rid>.json`` + ``<rid>.pdf`` per paper into ``out_dir``, for the browser upload."""
    import shutil
    out_dir.mkdir(parents=True, exist_ok=True)
    done = []
    for rid in rids:
        pdf = pdf_dir / f"{rid}.pdf"
        if not pdf.exists():
            print(f"  ! {rid}: no PDF under {pdf_dir}; skipped"); continue
        try:
            b = bundle_for(project, rid, with_runs=with_runs, stale_plan=stale_plan)
        except FileNotFoundError as e:
            print(f"  ! {e}; skipped"); continue
        (out_dir / f"{rid}.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
        shutil.copyfile(pdf, out_dir / f"{rid}.pdf")
        done.append(rid)
        print(f"  {rid}: {len(b['pages'])} pages, {len(b['outputs'])} outputs, {len(b['runs'])} readings -> {out_dir / (rid + '.json')}")
    return done


# ── the server ──────────────────────────────────────────────────────────────────────────────

def client(url: str, session: str = "chain-seed"):
    import httpx
    return httpx.Client(base_url=url, timeout=httpx.Timeout(1800.0, connect=10.0), headers={"X-Session-Id": session})


def resolve_schema(c, preset_id: str) -> str:
    try:
        for row in c.get("/api/presets", params={"usage": "import"}).json().get("presets", []):
            if ((row.get("spec") or {}).get("id") or row.get("id")) == preset_id and row.get("schema_id"):
                return row["schema_id"]
    except Exception:  # noqa: BLE001
        pass
    return preset_id


def ensure_dataset(c, title: str, schema_id: str) -> tuple[str, str]:
    for d in c.get("/api/datasets").json()["datasets"]:
        if d["title"] == title and d.get("schema_id") == schema_id:
            return d["id"], "reused"
    r = c.post("/api/datasets", json={"title": title, "schema_id": schema_id, "visibility": "private"})
    r.raise_for_status()
    ds = r.json()["id"]
    r = c.patch(f"/api/datasets/{ds}", json={"workflow": WORKFLOW})
    r.raise_for_status()
    return ds, "created"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def seed(url: str, project: Path, rids: list[str], *, title: str, pdf_dir: Path, with_runs: bool = True, stale_plan: bool = False, dry: bool = False) -> dict:
    project, pdf_dir = Path(project), Path(pdf_dir)
    papers = []
    for rid in rids:
        pdf = pdf_dir / f"{rid}.pdf"
        if not pdf.exists():
            print(f"  ! {rid}: no PDF under {pdf_dir}; skipped"); continue
        try:
            sup = supplies(project, rid, with_runs=with_runs, stale_plan=stale_plan)
        except FileNotFoundError as e:
            print(f"  ! {e}; skipped"); continue
        papers.append({"rid": rid, "pdf": pdf, "bytes": pdf.read_bytes(), "supplies": sup, "pages": pages_for(project, rid), "meta": paper_meta(project, rid)})
    for p in papers:
        p["sha"] = sha256(p["bytes"])
        print(f"  {p['rid']}: {len(p['pages'])} pages, {len(p['supplies'])} outputs ({sum(1 for s in p['supplies'] if s['role'] == 'run')} runs), "
              f"plan {'stale' if (p['supplies'][-1]['output'].get('stale')) else 'ok' if p['supplies'][-1]['output'].get('units') else 'none'}")
    if dry or not papers:
        return {"papers": [p["rid"] for p in papers], "dry": True}
    c = client(url)
    schema_id = resolve_schema(c, PRESET)
    ds, how = ensure_dataset(c, title, schema_id)
    print(f"dataset {how}: {url}/dataset?id={ds}  ({title}, {schema_id}, workflow {WORKFLOW})")
    staged = [{"sha256": p["sha"], "filename": p["pdf"].name} for p in papers]
    r = c.post(f"/api/datasets/{ds}/workflow/runs", json={"new_pdfs": staged, "use_credits": False})
    if r.status_code != 200:
        raise SystemExit(f"could not start the run: {r.status_code} {r.text[:300]}")
    run = r.json(); rid_run = run["id"]
    print(f"run {rid_run}: {run.get('jobs', {}).get('total')} jobs planned")
    done, failed = [], []
    for p in papers:
        m = p["meta"]
        r = c.post(f"/api/workflow-runs/{rid_run}/papers", files={"pdf": (p["pdf"].name, p["bytes"], "application/pdf")},
                   data={k: v for k, v in {"dispatch": "false", "pages": json.dumps(p["pages"], ensure_ascii=False), "title": m["title"], "doi": m["doi"],
                                             "abstract": m["abstract"], "journal": m["journal"], "year": m["year"], "authors": json.dumps(m["authors"], ensure_ascii=False),
                                             "peer_reviewed": m["peer_reviewed"]}.items() if v not in (None, "")})
        if r.status_code != 200:
            print(f"  ✗ {p['rid']}: staging failed {r.status_code} {r.text[:200]}"); failed.append(p["rid"]); continue
        ok = True
        for s in p["supplies"]:
            body = {"output": s["output"], "role": s["role"], "pass_index": s["pass_index"], "prompt_sha256": s["prompt_sha256"], "stability": s["stability"]}
            r = c.post(f"/api/workflow-runs/{rid_run}/passes/{s['step']}/{p['sha']}", json=body)
            if r.status_code != 200:
                print(f"  ✗ {p['rid']}: {s['step']} ({s['role']} {s['pass_index']}) refused {r.status_code} {r.text[:200]}"); ok = False; break
        (done if ok else failed).append(p["rid"])
        print(f"  {'✓' if ok else '✗'} {p['rid']}")
    view = c.get(f"/api/workflow-runs/{rid_run}").json()
    print(f"run {view.get('status')}: " + "; ".join(f"{s['step_id']} {s['status']}" for s in view.get("steps") or []))
    for s in view.get("steps") or []:
        if s.get("error"):
            print(f"  step {s['step_id']}: {s['error']}")
    for pp in view.get("papers") or []:
        if pp.get("error"):
            print(f"  paper {pp.get('pdf_sha256', '')[:12]} {pp.get('step_id')}: {pp['error']}")
    return {"dataset": ds, "run": rid_run, "done": done, "failed": failed, "status": view.get("status")}


def _stage_and_supply(c, run_id: str, p: dict) -> bool:
    """One paper into a run: the PDF staged without dispatch (its text and identity along), then
    its outputs supplied in order. True when every call landed."""
    m = p["meta"]
    r = c.post(f"/api/workflow-runs/{run_id}/papers", files={"pdf": (p["pdf"].name, p["bytes"], "application/pdf")},
               data={k: v for k, v in {"dispatch": "false", "pages": json.dumps(p["pages"], ensure_ascii=False), "title": m["title"], "doi": m["doi"],
                                       "abstract": m["abstract"], "journal": m["journal"], "year": m["year"], "authors": json.dumps(m["authors"], ensure_ascii=False),
                                       "peer_reviewed": m["peer_reviewed"]}.items() if v not in (None, "")})
    if r.status_code != 200:
        print(f"  ✗ {p['rid']}: staging failed {r.status_code} {r.text[:200]}"); return False
    for s in p["supplies"]:
        body = {"output": s["output"], "role": s["role"], "pass_index": s["pass_index"], "prompt_sha256": s["prompt_sha256"], "stability": s["stability"]}
        r = c.post(f"/api/workflow-runs/{run_id}/passes/{s['step']}/{p['sha']}", json=body)
        if r.status_code != 200:
            print(f"  ✗ {p['rid']}: {s['step']} ({s['role']} {s['pass_index']}) refused {r.status_code} {r.text[:200]}"); return False
    print(f"  ✓ {p['rid']}")
    return True


def _load_papers(project: Path, rids: list[str], pdf_dir: Path, *, with_runs: bool, stale_plan: bool) -> list[dict]:
    papers = []
    for rid in rids:
        pdf = pdf_dir / f"{rid}.pdf"
        if not pdf.exists():
            print(f"  ! {rid}: no PDF under {pdf_dir}; skipped"); continue
        try:
            sup = supplies(project, rid, with_runs=with_runs, stale_plan=stale_plan)
        except FileNotFoundError as e:
            print(f"  ! {e}; skipped"); continue
        b = pdf.read_bytes()
        papers.append({"rid": rid, "pdf": pdf, "bytes": b, "sha": sha256(b), "supplies": sup, "pages": pages_for(project, rid), "meta": paper_meta(project, rid)})
        plan = papers[-1]["supplies"][-1]["output"]
        print(f"  {rid}: {len(papers[-1]['pages'])} pages, {len(sup)} outputs ({sum(1 for s in sup if s['role'] == 'run')} runs), "
              f"plan {'stale' if plan.get('stale') else 'ok' if plan.get('units') else 'none'}")
    return papers


def _print_run(c, wid: str, run_id: str) -> dict:
    view = c.get(f"/api/workflow-runs/{run_id}").json()
    print(f"run {view.get('status')}: " + "; ".join(f"{s['step_id']} {s['status']}" for s in view.get("steps") or []))
    for s in view.get("steps") or []:
        if s.get("error"):
            print(f"  step {s['step_id']}: {s['error']}")
    for pp in view.get("papers") or []:
        if pp.get("error"):
            print(f"  paper {str(pp.get('pdf_sha256') or '')[:12]} {pp.get('step_id')}: {pp['error']}")
    tasks_now = c.get(f"/api/projects/{wid}/tasks", params={"run": run_id}).json().get("tasks") or [] if wid else []
    gate = lambda t: t.get("gate") or {}  # noqa: E731
    if tasks_now:
        print("tasks: " + "; ".join(f"{t['step_id']} {gate(t).get('checked', 0)}/{gate(t).get('total', 0)}" + (f" (waits for {gate(t)['needs_coders']} coders)" if gate(t).get("needs_coders") else "") for t in tasks_now))
    return view


def seed_project(base: str, m: dict, root: Path, project: Path, *, workflow: str, title: str, rids: list[str] | None, pdf_dir: Path | None,
                 with_runs: bool = True, stale_plan: bool = False, local: bool = False, dry: bool = False) -> dict:
    """The project mode (the v4 seeder's, for the chain): owner, project, dataset, people, tasks,
    then the papers staged and supplied into a run of the chain."""
    import httpx
    sys.path.insert(0, str(HERE))
    from seed_accounts import sign_in  # noqa: PLC0415
    local = local or bool(m.get("local"))
    owner = m.get("owner") or {}
    if not local and not (owner.get("email") and owner.get("password")):
        raise SystemExit("the owner is missing `email` or `password` (or say `local: true` for an engine in local mode)")
    coders = [] if local else [{"email": x} if isinstance(x, str) else dict(x) for x in (m.get("coders") or [])]
    tasks = m.get("tasks") or {}
    only = (m.get("only") or {}).get("extraction") or []
    rids = list(rids) if rids else list(only)
    folder = (m.get("papers") or {}).get("extraction")
    pdf_dir = pdf_dir or (root / folder if folder else project / "labeling" / "metalens" / "import_pilot")
    print(f"{'the local owner' if local else owner['email']} → project “{title}” of workflow “{workflow}” on {base}")
    papers = _load_papers(project, rids, pdf_dir, with_runs=with_runs, stale_plan=stale_plan)
    if dry:
        print(f"  would add {len(coders)} person(s); tasks: " + ", ".join(f"{k} coders={json.dumps((v or {}).get('coders'))}" for k, v in tasks.items()))
        return {"dry": True, "papers": [p["rid"] for p in papers]}
    with httpx.Client(base_url=base, timeout=httpx.Timeout(1800.0, connect=30.0), headers=({"X-Session-Id": "chain-seed"} if local else {})) as c:
        if not local:
            print(f"  {sign_in(c, owner['email'], owner['password'])}")
        mine = c.get("/api/projects").json().get("projects") or []
        wf_of = lambda w: (w.get("workflow") or {}).get("id") if isinstance(w.get("workflow"), dict) else str(w.get("workflow") or "").split("@")[0]  # noqa: E731
        proj = next((w for w in mine if w.get("title") == title and (not wf_of(w) or wf_of(w) == workflow)), None)
        if proj is None:
            r = c.post("/api/projects", json={"workflow": workflow, "title": title})
            if r.status_code != 200:
                raise SystemExit(f"could not make the project: {r.status_code} {r.text[:200]}")
            proj, how = r.json(), "made"
        else:
            how = "found"
        wid = proj["id"]
        print(f"  {how}: {base}/project?id={wid}")
        page = c.get(f"/api/projects/{wid}").json()
        if page.get("kind") != "project":
            raise SystemExit(f"“{workflow}” runs over one preset, so “{title}” is a plain dataset, not a project")
        for item in page.get("members") or []:
            if not item.get("dataset"):
                r = c.post(f"/api/datasets/{wid}/group/link", json={"role": item["role"], "create": True})
                if r.status_code != 200:
                    raise SystemExit(f"could not link a dataset for {item['role']}: {r.status_code} {r.text[:200]}")
                print(f"  linked a new dataset for {item['role']}")
        have = {str(x.get("email") or "").lower() for x in c.get(f"/api/projects/{wid}/people").json().get("people") or []} if coders else set()
        for x in coders:
            if x["email"].lower() in have:
                continue
            r = c.post(f"/api/projects/{wid}/people", json={"email": x["email"]})
            print(f"  {'person added' if r.status_code == 200 else 'person NOT added'}: {x['email']}{'' if r.status_code == 200 else ' — ' + r.text[:120]}")
        for step_id, cfg in tasks.items():
            want = (cfg or {}).get("coders")
            r = c.patch(f"/api/projects/{wid}/tasks/{step_id}", json={"coders": want})
            print(f"  task {step_id}: {'coders = ' + json.dumps(want) if r.status_code == 200 else 'NOT set — ' + r.text[:120]}")
        panel = c.get(f"/api/datasets/{wid}/workflow").json()
        side = next((s for s in panel.get("steps") or [] if s.get("kind") == "compute" and s.get("role") == "extraction"), None)
        have_docs = {d.get("filename") for d in c.get("/api/documents", params={"dataset": side["dataset_id"], "limit": 5000}).json().get("documents") or []} if side and side.get("dataset_id") else set()
        pending = [p for p in papers if p["pdf"].name not in have_docs]
        run = panel.get("current_run")
        live = bool(run) and run.get("status") not in ("done", "failed", "cancelled")
        if not pending:
            print(f"  nothing to upload: {len(papers)} paper(s) already there" + (f"; the latest run ({run['id']}, {run.get('status')}) stands" if run else ""))
            if run:
                _print_run(c, wid, run["id"])
            if not local:
                c.post("/api/auth/logout")
            return {"project": wid, "run": run["id"] if run else None, "done": [], "skipped": [p["rid"] for p in papers]}
        if live:
            print(f"  a run is in progress ({run['id']}); its plan is fixed — cancel it or wait before seeding more papers")
            if not local:
                c.post("/api/auth/logout")
            return {"project": wid, "run": run["id"], "done": [], "skipped": [p["rid"] for p in papers]}
        staged = [{"sha256": p["sha"], "filename": p["pdf"].name} for p in pending]
        r = c.post(f"/api/datasets/{wid}/workflow/runs", json={"new_pdfs": staged, "use_credits": False})
        if r.status_code != 200:
            raise SystemExit(f"could not start the run: {r.status_code} {r.text[:300]}")
        run = r.json()
        print(f"  run started: {run['id']} ({run.get('jobs', {}).get('total')} jobs)")
        done, failed = [], []
        for p in pending:
            (done if _stage_and_supply(c, run["id"], p) else failed).append(p["rid"])
        view = _print_run(c, wid, run["id"])
        if not local:
            c.post("/api/auth/logout")
        return {"project": wid, "run": run["id"], "done": done, "failed": failed, "skipped": [p["rid"] for p in papers if p not in pending], "status": view.get("status")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", help="the review project's root (required without --manifest; else the manifest's repository)")
    ap.add_argument("--manifest", type=Path, help="a project manifest (seed_accounts.py's): owner, coders, papers, only, tasks")
    ap.add_argument("--workflow", default=WORKFLOW, help="with --manifest: the workflow of the project (default genai-work-chain)")
    ap.add_argument("--base", help="with --manifest: the server (overrides the manifest's `base`)")
    ap.add_argument("--local", action="store_true", help="with --manifest: an engine in local mode, no accounts")
    ap.add_argument("--rid", action="append", default=[])
    ap.add_argument("--ids", help="a file of record ids, one per line")
    ap.add_argument("--url", default="http://127.0.0.1:8775")
    ap.add_argument("--title", default="GenAI chain pilot")
    ap.add_argument("--pdfs", help="the folder holding <rid>.pdf (default: the project's labeling/metalens/import_pilot)")
    ap.add_argument("--no-runs", action="store_true", help="supply the primaries only, not the second and third readings")
    ap.add_argument("--stale-plans", action="store_true", help="supply a headline plan file even when it predates the paper's current extraction")
    ap.add_argument("--dry", action="store_true", help="read the files and report, send nothing")
    ap.add_argument("--write-bundles", type=Path, help="write <rid>.json + <rid>.pdf per paper into this folder for the project page's “Upload papers with results”, send nothing")
    a = ap.parse_args()
    rids = list(a.rid) + ([x for x in Path(a.ids).read_text().split() if x] if a.ids else [])
    if a.write_bundles:
        if a.manifest:
            m = json.loads(a.manifest.read_text(encoding="utf-8")); root = a.manifest.resolve().parent
            project = Path(a.project).resolve() if a.project else next((d for d in root.parents if (d / "labeling" / "split").is_dir()), None)
            rids = rids or list((m.get("only") or {}).get("extraction") or [])
            folder = (m.get("papers") or {}).get("extraction")
            pdf_dir = Path(a.pdfs) if a.pdfs else (root / folder if folder else project / "labeling" / "metalens" / "import_pilot")
        else:
            if not a.project or not rids:
                ap.error("--write-bundles needs --project and --rid/--ids (or a --manifest)")
            project = Path(a.project).resolve(); pdf_dir = Path(a.pdfs) if a.pdfs else project / "labeling" / "metalens" / "import_pilot"
        done = write_bundles(project, rids, a.write_bundles, pdf_dir, with_runs=not a.no_runs, stale_plan=a.stale_plans)
        return 0 if done else 1
    if a.manifest:
        m = json.loads(a.manifest.read_text(encoding="utf-8"))
        root = a.manifest.resolve().parent
        project = Path(a.project).resolve() if a.project else next((d for d in root.parents if (d / "labeling" / "split").is_dir()), None)
        if project is None:
            ap.error("--project: the review project's root could not be found above the manifest")
        base = (a.base or m.get("base") or "").rstrip("/")
        if not base:
            ap.error("no server: set `base` in the manifest or pass --base")
        title = a.title if a.title != "GenAI chain pilot" else (m.get("title") or a.workflow)
        out = seed_project(base, m, root, project, workflow=a.workflow, title=title, rids=rids or None, pdf_dir=Path(a.pdfs) if a.pdfs else None,
                           with_runs=not a.no_runs, stale_plan=a.stale_plans, local=a.local, dry=a.dry)
        return 0 if not out.get("failed") else 1
    if not a.project:
        ap.error("--project is required without --manifest")
    project = Path(a.project).resolve()
    if not rids:
        ap.error("name papers with --rid or --ids")
    pdf_dir = Path(a.pdfs) if a.pdfs else project / "labeling" / "metalens" / "import_pilot"
    out = seed(a.url, project, rids, title=a.title, pdf_dir=pdf_dir, with_runs=not a.no_runs, stale_plan=a.stale_plans, dry=a.dry)
    return 0 if not out.get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
