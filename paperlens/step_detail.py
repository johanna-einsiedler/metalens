"""One step of a dataset's workflow, in detail: how it is set up (its declaration) and, run by
run, which papers it ran on and what came of each — what the Workflow card shows under the
diagram when a step is clicked. Read only; one call per click, so the panel itself stays lean."""
from __future__ import annotations

import psycopg

from . import passes, records, review_gate
from . import workflow_spec as ws
from . import workflows

MAX_RUNS = 20


def _labels(conn, run_id: str, doc_ids: list[str], shas: list[str]) -> tuple[dict, dict]:
    """What to call a paper: its title (else its file name), its DOI — by document and by PDF."""
    by_doc: dict[str, dict] = {}
    by_sha: dict[str, dict] = {}
    if doc_ids:
        for did, fn, sha, title, doi, year in conn.execute(
                """SELECT ed.id::text, ed.filename, ed.pdf_sha256, p.title, p.doi, p.year FROM extraction_document ed
                   LEFT JOIN paper p ON p.id = ed.paper_id WHERE ed.id = ANY(%s::uuid[])""", (doc_ids,)).fetchall():
            by_doc[did] = {"title": title, "filename": fn, "doi": doi, "year": year, "pdf_sha256": sha}
    if shas:
        for sha, title, fn, doi in conn.execute(
                "SELECT pdf_sha256, title, filename, doi FROM workflow_paper WHERE run_id = %s::uuid AND pdf_sha256 = ANY(%s)", (run_id, shas)).fetchall():
            by_sha[sha] = {"title": title, "filename": fn, "doi": doi}
        missing = [s for s in shas if s not in by_sha]
        if missing:
            for sha, fn, title, doi in conn.execute(
                    """SELECT DISTINCT ON (ed.pdf_sha256) ed.pdf_sha256, ed.filename, p.title, p.doi FROM extraction_document ed
                       LEFT JOIN paper p ON p.id = ed.paper_id WHERE ed.pdf_sha256 = ANY(%s) ORDER BY ed.pdf_sha256, ed.created_at DESC""", (missing,)).fetchall():
                by_sha[sha] = {"title": title, "filename": fn, "doi": doi}
    return by_doc, by_sha


def _paper(label: dict | None, **extra) -> dict:
    lab = label or {}
    return {"title": lab.get("title"), "filename": lab.get("filename"), "doi": lab.get("doi"), **extra}


def _papers_of_jobs(conn, run: dict, step_id: str, *, owner: bool) -> list[dict]:
    js = workflows.jobs(conn, run["id"], step_id)
    by_doc, by_sha = _labels(conn, run["id"], [j["document_id"] for j in js if j.get("document_id")], [j["pdf_sha256"] for j in js])
    out = []
    for j in js:
        lab = by_doc.get(j.get("document_id")) or by_sha.get(j["pdf_sha256"]) or {"filename": j.get("filename")}
        out.append(_paper(lab, document_id=j.get("document_id"), pdf_sha256=j["pdf_sha256"], status=j["status"],
                          **({"error": j["error"]} if owner and j.get("error") else {})))
    return out


def _with_records(conn, papers: list[dict]) -> None:
    ids = [p["document_id"] for p in papers if p.get("document_id")]
    if not ids:
        return
    n = dict(conn.execute("""SELECT document_id::text, count(*) FROM record WHERE document_id = ANY(%s::uuid[]) AND NOT COALESCE(screened_empty, false)
                             GROUP BY document_id""", (ids,)).fetchall())
    for p in papers:
        if p.get("document_id"):
            p["n_records"] = int(n.get(p["document_id"], 0))


def _run_papers(conn, run: dict, sr: dict, st: dict, srs: dict, *, owner: bool) -> tuple[list[dict], dict]:
    """The papers one step-run covered, each with its outcome, and a summary of the step-run."""
    kind = st.get("kind")
    out = sr.get("outputs") or {}
    if kind == "pass":
        rows = [o for o in passes.outputs_of(conn, run["id"], st["id"]) if o["role"] == "primary"]
        failed_jobs = {j["pdf_sha256"]: j for j in workflows.jobs(conn, run["id"], st["id"]) if j["status"] == "failed"}
        shas = list(dict.fromkeys([o["pdf_sha256"] for o in rows] + list(failed_jobs)))
        _d, by_sha = _labels(conn, run["id"], [], shas)
        by_out = {o["pdf_sha256"]: o for o in rows}
        papers = []
        for sha in shas:
            o = by_out.get(sha) or {}
            err = (o or failed_jobs.get(sha) or {}).get("error")
            how = "supplied" if o.get("supplied") else "reused" if o.get("reused_from") else None
            papers.append(_paper(by_sha.get(sha), pdf_sha256=sha, status=o.get("status") or "failed", how=how, n_calls=o.get("n_calls", 0),
                                 findings=o.get("findings", 0), model=o.get("model"), **({"error": err} if owner and err else {})))
        return papers, {"n_papers": len(papers), "n_calls": sum(p["n_calls"] for p in papers), "failed": sum(1 for p in papers if p["status"] == "failed")}
    if kind in ("extract", "agent", "compute"):
        papers = _papers_of_jobs(conn, run, st["id"], owner=owner)
        if not papers and out.get("document_ids"):
            by_doc, _s = _labels(conn, run["id"], list(out["document_ids"]), [])
            papers = [_paper(by_doc.get(did), document_id=did, status="done") for did in out["document_ids"]]
        _with_records(conn, papers)
        return papers, {k: out[k] for k in ("n_records", "attached", "failed", "n_documents") if k in out}
    if kind == "check":
        f_doc: dict[str, dict] = {}                                  # findings by severity, per document (or per PDF, over passes)
        f_sha: dict[str, dict] = {}
        for did, sha, sev, n in conn.execute(
                """SELECT document_id::text, pdf_sha256, severity, count(*) FROM workflow_finding WHERE step_run_id = %s::uuid
                   GROUP BY document_id, pdf_sha256, severity""", (sr["id"],)).fetchall():
            c = f_doc.setdefault(did, {}) if did else f_sha.setdefault(sha or "", {})
            c[sev] = c.get(sev, 0) + int(n)
        docs: list[str] = []                                         # the papers it read: what its `over` steps made in this run
        shas: list[str] = []
        for o in st.get("over") or []:
            osr = srs.get(o) or {}
            if osr.get("kind") == "pass":
                shas += [x["pdf_sha256"] for x in passes.outputs_of(conn, run["id"], o) if x["role"] == "primary"]
            docs += list((osr.get("outputs") or {}).get("document_ids") or [])
        docs = list(dict.fromkeys(docs + list(f_doc)))
        shas = list(dict.fromkeys(shas + [s for s in f_sha if s]))
        by_doc, by_sha = _labels(conn, run["id"], docs, shas)
        papers = [_paper(by_doc.get(d), document_id=d, status="checked", findings=f_doc.get(d, {})) for d in docs]
        papers += [_paper(by_sha.get(s), pdf_sha256=s, status="checked", findings=f_sha.get(s, {})) for s in shas]
        return papers, {k: out[k] for k in ("n_checked", "findings", "by_severity", "by_code") if k in out}
    if kind == "review":
        if not sr.get("review"):
            return [], {}
        per: dict[str, dict] = {}
        for it in review_gate.items(conn, sr):
            p = per.setdefault(it.get("document_id") or "", {"items": 0, "checked": 0, "disagreed": 0, "coders": {}})
            p["items"] += 1; p["checked"] += 1 if it["checked"] else 0; p["disagreed"] += 1 if it["checked"] and it["disagreed"] else 0
            if it.get("coder"):
                c = p["coders"].setdefault(it["coder"], {"label": it["coder"], "items": 0, "checked": 0})
                c["items"] += 1; c["checked"] += 1 if it["checked"] else 0
        by_doc, _s = _labels(conn, run["id"], [d for d in per if d], [])
        papers = [_paper(by_doc.get(d), document_id=d or None, status="done" if p["checked"] >= p["items"] else "open",
                         items=p["items"], checked=p["checked"], disagreed=p["disagreed"], coders=list(p["coders"].values())) for d, p in per.items()]
        g = review_gate.state(conn, sr)
        summary = {k: g.get(k) for k in ("mode", "unit", "total", "checked", "pending", "disagreed", "satisfied", "overridden_by", "n_target", "frame_n") if g.get(k) is not None}
        summary["queue"] = f"/workspace?queue={run['id']}:{st['id']}"
        return papers, summary
    return [], {k: v for k, v in out.items() if isinstance(v, (int, float, str, bool)) and k != "derived_id"}


def detail(conn: psycopg.Connection, dataset_id: str, step_id: str, *, owner: bool) -> dict | None:
    """``{step, kind, implicit, runs: [{run_id, created_at, run_status, status, started_at,
    finished_at, model, n_calls, summary, papers: [{title, filename, doi, document_id |
    pdf_sha256, status, …}]}]}``, newest run first; None when the dataset's workflow has no such step."""
    d = records.get_dataset(conn, dataset_id)
    if d is None:
        return None
    spec, implicit = workflows.resolve(conn, d)
    st = ws.steps_by_id(spec).get(step_id)
    if st is None:
        return None
    runs = []
    for run in workflows.list_runs(conn, dataset_id, limit=MAX_RUNS):
        srs = {x["step_id"]: x for x in workflows.step_runs(conn, run["id"])}
        sr = srs.get(step_id)
        if sr is None or sr["status"] == "pending":                      # the step never ran in this run
            continue
        rst = ws.steps_by_id(run.get("workflow") or {}).get(step_id) or st   # as that run declared it
        papers, summary = _run_papers(conn, run, sr, rst, srs, owner=owner)
        runs.append({"run_id": run["id"], "created_at": run.get("created_at"), "run_status": run["status"], "status": sr["status"],
                     "started_at": sr.get("started_at"), "finished_at": sr.get("finished_at"), "model": sr.get("model") or run.get("model"),
                     "n_calls": sr.get("n_calls") or 0, "contract": run.get("contract"), "summary": summary, "papers": papers,
                     **({"error": sr["error"]} if owner and sr.get("error") else {})})
    out = {"step": st, "kind": st.get("kind"), "implicit": implicit, "workflow": {"id": spec.get("id"), "contract": ws.workflow_id(spec)}, "runs": runs}
    if st.get("kind") == "check":                                        # what the registered check reads and finds
        from . import checks
        reg = checks.REGISTRY.get(st.get("check")) or {}
        out["check_info"] = {"label": reg.get("label"), "codes": reg.get("codes") or {}}
    return out
