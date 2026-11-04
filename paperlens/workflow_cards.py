"""The public workflow inventory: one card per curated workflow (one with a card), and what a
card shows that is not written in its markdown — the steps with their pinned presets (linked to
the preset cards), the review policy, the checks with their codes, the releases and citations,
the public datasets built with the workflow with their badges and last runs, and a verification
roll-up over those datasets."""
from __future__ import annotations

import psycopg

from . import (
    brands,
    checks,
    preset_cards,
    workflow_bundle,
    workflow_releases,
    workflows,
)
from . import preset_spec as ps
from . import workflow_spec as ws


def card_workflows(brand: brands.Brand | None = None) -> dict[str, dict]:
    return {wid: spec for wid, spec in workflows.load_all().items()
            if ws.has_card(spec) and (brand is None or brand.shows_preset(spec.get("meta")))}


def card_url(workflow_id: str) -> str:
    return f"{brands.public_url()}/workflow?id={workflow_id}"


def _public_datasets(conn: psycopg.Connection, wid: str) -> list[dict]:
    """The public catalogue datasets that declare this workflow (any contract of it)."""
    rows = conn.execute(
        """SELECT d.id::text, d.slug, d.title, d.description, d.workflow, d.github_source, d.published_meta, d.created_at,
                  (SELECT count(DISTINCT r.paper_id) FROM record r WHERE r.dataset_id = d.id AND NOT COALESCE(r.screened_empty, false)),
                  (SELECT count(*) FROM record r WHERE r.dataset_id = d.id AND NOT COALESCE(r.screened_empty, false))
           FROM dataset d
           WHERE d.visibility = 'public' AND d.publish_status = 'published' AND d.catalogue AND split_part(d.workflow, '@', 1) = %s
           ORDER BY d.created_at DESC""", (wid,)).fetchall()
    out = []
    for did, slug, title, desc, wf, ghs, pmeta, created, n_papers, n_records in rows:
        out.append({"id": did, "slug": slug, "title": title, "description": desc, "contract": wf, "github_source": bool(ghs),
                    "published_meta": pmeta, "created_at": created.isoformat() if created else None, "n_papers": int(n_papers or 0), "n_records": int(n_records or 0)})
    return out


def datasets_for(conn: psycopg.Connection, wid: str) -> list[dict]:
    from . import records, releases
    out = []
    for d in _public_datasets(conn, wid):
        cred = preset_cards._credibility(conn, d)   # noqa: SLF001
        last = workflows.last_done_run(conn, d["id"])
        rels = [{"number": r["number"], "created_at": r["created_at"], "credibility": r.get("credibility"),
                 "doi": r["doi"] if preset_cards._counts(r.get("doi")) else None, "workflow_run_id": r.get("workflow_run_id")}   # noqa: SLF001
                for r in releases.list_for_dataset(conn, d["id"])]
        out.append({"id": d["id"], "slug": d["slug"], "title": d["title"], "description": d["description"], "contract": d["contract"],
                    "github_source": d["github_source"], "published_url": records.dataset_github_url(d["slug"]) if d["github_source"] else None,
                    "credibility": {k: cred.get(k) for k in ("tier", "label", "label_long", "n_records", "audited", "audited_pct", "agreement", "agreement_ci", "agreed", "sample", "checks")},
                    "n_records": d["n_records"], "n_papers": d["n_papers"], "releases": rels,
                    "last_run": {"id": last["id"], "status": last["status"], "finished_at": last.get("finished_at"), "release": last.get("workflow_release")} if last else None,
                    "n_runs": len(workflows.list_runs(conn, d["id"], limit=100))})
    return out


def _release_summary(conn: psycopg.Connection, wid: str, spec: dict) -> dict:
    rels = workflow_releases.list_for_workflow(conn, wid)
    latest = rels[0] if rels else None
    concept = workflow_releases.concept_doi(conn, wid)
    url = card_url(wid)
    return {"releases": [{**workflow_releases.public_row(r), "bundle_url": f"/api/workflows/{wid}/releases/{r['number']}/bundle"} for r in rels],
            "latest": workflow_releases.public_row(latest) if latest else None, "concept_doi": concept,
            "cite_as": workflow_releases.citation(spec, latest, concept_doi=concept, url=url),
            "cite_this_version": workflow_releases.citation_for_version(spec, latest, url=url) if latest else None,
            "changed_since_release": workflow_releases.changed_since(spec, latest) if latest else None}


def _steps(conn: psycopg.Connection, spec: dict) -> list[dict]:
    from . import presets
    out = []
    for st in spec.get("steps") or []:
        item = {k: st.get(k) for k in ("id", "kind", "label", "help", "preset", "source", "k", "check", "over", "on_fail", "severity", "params", "gate", "column", "unit", "group", "keyed_by", "dataset")}
        item = {k: v for k, v in item.items() if v is not None}
        if st.get("kind") in ws.REVIEWED_KINDS:
            item["review"] = ws.review_of(st)
        if st.get("preset"):
            base = ws.preset_base(st["preset"])
            meta = presets.get(base, conn) or {}
            pspec = meta.get("spec") if isinstance(meta, dict) else None
            item["preset_card"] = {"id": base, "title": (meta.get("title") if isinstance(meta, dict) else None) or base,
                                   "has_card": bool(pspec and ps.has_card(pspec)),
                                   "url": f"/preset?id={base}", "live_schema_id": meta.get("schema_id") if isinstance(meta, dict) else None}
        if st.get("kind") == "check":
            reg = checks.REGISTRY.get(st.get("check")) or {}
            item["check_info"] = {"label": reg.get("label"), "codes": reg.get("codes") or {}, "needs_companion": bool(reg.get("needs_companion"))}
        if (st.get("input") or {}).get("previous"):
            item["reads"] = list(st["input"]["previous"])
        out.append(item)
    return out


def inventory(conn: psycopg.Connection, brand: brands.Brand | None = None) -> list[dict]:
    out = []
    for wid, spec in card_workflows(brand).items():
        meta = spec.get("meta") or {}
        ds = _public_datasets(conn, wid)
        n_runs = conn.execute("SELECT count(*) FROM workflow_run WHERE workflow_id = %s AND status = 'done'", (wid,)).fetchone()[0]
        out.append({"id": wid, "title": meta.get("title"), "tagline": meta.get("tagline"), "description": meta.get("description"),
                    "hidden": bool(meta.get("hidden")), "authors": list(meta.get("authors") or []), "keywords": list(meta.get("keywords") or []),
                    "license": meta.get("license"), "contract": ws.workflow_id(spec), "version": spec.get("version", 1),
                    "steps": [{"id": st["id"], "kind": st["kind"], "label": st.get("label")} for st in spec["steps"]],
                    "presets": [{"step": s, "schema_id": p} for s, p in ws.pins(spec)],
                    "review": [{"step": st["id"], **ws.review_of(st)} for st in spec["steps"] if st.get("kind") in ws.REVIEWED_KINDS and ws.review_of(st).get("mode") != "none"],
                    **_release_summary(conn, wid, spec),
                    "counts": {"datasets": len(ds), "papers": sum(d["n_papers"] for d in ds), "records": sum(d["n_records"] for d in ds), "runs": int(n_runs)}})
    out.sort(key=lambda x: (0 if x["latest"] else 1, (x["title"] or "").lower()))
    return out


def card(conn: psycopg.Connection, workflow_id: str, *, viewer_is_admin: bool = False, user: dict | None = None) -> dict | None:
    wid = workflow_id.partition("@")[0]
    spec = card_workflows().get(wid)
    if spec is None:
        return None
    meta = spec.get("meta") or {}
    cm = ps.card_meta(spec)
    datasets = datasets_for(conn, wid)
    errs, warns = workflows.validate_for(conn, spec)
    out = {"id": wid, "title": meta.get("title"), "tagline": meta.get("tagline"), "description": meta.get("description"),
           "hidden": bool(meta.get("hidden")), "brands": list(meta.get("brands") or []),
           "authors": cm["authors"], "keywords": cm["keywords"], "license": cm["license"], "related": cm["related"], "card_md": cm["text"],
           "contract": ws.workflow_id(spec), "version": spec.get("version", 1), "card_url": card_url(wid),
           "steps": _steps(conn, spec), "later": workflows.later_problems(spec), "errors": errs, "warnings": warns,
           "steps_md": workflow_bundle.steps_markdown(spec, workflow_releases.pinned_presets(conn, spec)),
           **_release_summary(conn, wid, spec),
           "datasets": datasets, "verification": preset_cards.rollup(datasets),
           "viewer": {"is_admin": bool(viewer_is_admin)}}
    if viewer_is_admin:
        from . import zenodo
        out["zenodo"] = zenodo.status(conn, user)
    return out


def dataset_dois_for(conn: psycopg.Connection, wid: str) -> list[str]:
    return [r["doi"] for d in datasets_for(conn, wid) for r in d["releases"] if r.get("doi")]
