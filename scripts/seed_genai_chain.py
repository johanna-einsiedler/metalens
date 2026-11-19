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
    design_hash = (snap.get("hashes") or {}).get("design")
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--rid", action="append", default=[])
    ap.add_argument("--ids", help="a file of record ids, one per line")
    ap.add_argument("--url", default="http://127.0.0.1:8775")
    ap.add_argument("--title", default="GenAI chain pilot")
    ap.add_argument("--pdfs", help="the folder holding <rid>.pdf (default: the project's labeling/metalens/import_pilot)")
    ap.add_argument("--no-runs", action="store_true", help="supply the primaries only, not the second and third readings")
    ap.add_argument("--stale-plans", action="store_true", help="supply a headline plan file even when it predates the paper's current extraction")
    ap.add_argument("--dry", action="store_true", help="read the files and report, send nothing")
    a = ap.parse_args()
    project = Path(a.project).resolve()
    rids = list(a.rid) + ([x for x in Path(a.ids).read_text().split() if x] if a.ids else [])
    if not rids:
        ap.error("name papers with --rid or --ids")
    pdf_dir = Path(a.pdfs) if a.pdfs else project / "labeling" / "metalens" / "import_pilot"
    out = seed(a.url, project, rids, title=a.title, pdf_dir=pdf_dir, with_runs=not a.no_runs, stale_plan=a.stale_plans, dry=a.dry)
    return 0 if not out.get("failed") else 1


if __name__ == "__main__":
    sys.exit(main())
