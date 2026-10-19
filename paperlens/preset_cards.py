"""The public preset inventory: one card per curated preset, and everything a card shows that
is not written in its markdown — which public datasets were built with the preset (and under
which version of it), the dashboards over those datasets, a verification roll-up computed from
the datasets' review events, releases and citations.

Linking is by preset id across every version (``split_part(schema_id, '@', 1)``, legacy ids
folded in — the rule ``records.documents_by_hashes`` uses), on public catalogue datasets only.
A dataset's records may sit on another version of the preset than ``dataset.schema_id`` (the
preset gained a field after the first papers), so versions are listed per dataset.
"""
from __future__ import annotations

import hashlib
import json

import psycopg

from . import brands, dashboards, external_dashboards, preset_releases, preset_spec as ps, presets, presets_legacy, records, releases


def bases_for(preset_id: str) -> list[str]:
    """The id and every id it was renamed from: all versions of one preset."""
    pid = presets_legacy.resolve_id(preset_id)
    return [pid] + [old for old, new in presets_legacy.LEGACY_IDS.items() if new == pid]


def card_presets(brand: brands.Brand | None = None) -> dict[str, dict]:
    """Every bundled preset with a card — hidden and import-mode ones included (a hidden MASEM
    variant and an import schema are both documented) — filtered by brand when one is given."""
    return {pid: spec for pid, spec in presets.load_all().items()
            if ps.has_card(spec) and (brand is None or brand.shows_preset(spec.get("meta")))}


def card_url(preset_id: str) -> str:
    return f"{brands.public_url()}/preset?id={preset_id}"


# ── datasets built with a preset ───────────────────────────────────────────────

def _public_datasets(conn: psycopg.Connection, bases: list[str]) -> list[dict]:
    """Public catalogue datasets with records under any version of the preset (or whose own
    schema is one), with per-version record and paper counts."""
    rows = conn.execute(
        """WITH pub AS (
             SELECT d.id, d.slug, d.title, d.description, d.schema_id, d.github_source, d.published_meta,
                    d.published_file_sha, d.git_pr_url, d.created_at
             FROM dataset d WHERE d.visibility = 'public' AND d.publish_status = 'published' AND d.catalogue),
           hit AS (
             SELECT r.dataset_id, r.schema_id,
                    count(*) FILTER (WHERE NOT COALESCE(r.screened_empty, false)) AS n_records,
                    count(DISTINCT r.paper_id) FILTER (WHERE NOT COALESCE(r.screened_empty, false)) AS n_papers
             FROM record r JOIN pub ON pub.id = r.dataset_id
             WHERE split_part(r.schema_id, '@', 1) = ANY(%s::text[])
             GROUP BY r.dataset_id, r.schema_id)
           SELECT pub.id::text, pub.slug, pub.title, pub.description, pub.schema_id, pub.github_source, pub.published_meta,
                  pub.published_file_sha, pub.git_pr_url, pub.created_at, hit.schema_id, hit.n_records, hit.n_papers
           FROM pub LEFT JOIN hit ON hit.dataset_id = pub.id
           WHERE hit.dataset_id IS NOT NULL OR split_part(pub.schema_id, '@', 1) = ANY(%s::text[])
           ORDER BY pub.created_at DESC, hit.schema_id""", (bases, bases)).fetchall()
    out: dict[str, dict] = {}
    for did, slug, title, desc, sid, ghs, pmeta, fsha, pr, created, rsid, nrec, npap in rows:
        d = out.setdefault(did, {"id": did, "slug": slug, "title": title, "description": desc, "schema_id": sid,
                                 "github_source": bool(ghs), "published_meta": pmeta if isinstance(pmeta, dict) else {},
                                 "published_url": records.dataset_github_url(slug) if (fsha or pr or ghs) else None,
                                 "created_at": created.isoformat() if created else None, "versions": [], "n_records": 0, "n_papers": 0})
        if rsid:
            d["versions"].append({"schema_id": rsid, "n_records": int(nrec or 0), "n_papers": int(npap or 0)})
            d["n_records"] += int(nrec or 0)
            d["n_papers"] = max(d["n_papers"], int(npap or 0))
    return list(out.values())


def _version_basis(conn: psycopg.Connection, pid: str, dataset_id: str, schema_id: str, live_schema_id: str) -> dict:
    """Which release of the preset produced records of this contract in this dataset: exact
    (the prompt a document was run with equals a release's, re-rendered with the document's own
    parameters), contract (the fields match one or several releases; the prompt could not be
    told apart — edited prompts, imports, GitHub-synced documents carry no hash), unreleased
    (the live declaration, never released), pre-release (an older contract from before any release)."""
    cands = preset_releases.by_schema(conn, pid, schema_id)
    if not cands:
        return {"basis": "unreleased" if schema_id == live_schema_id else "pre-release", "release": None, "candidates": []}
    docs = conn.execute(
        """SELECT DISTINCT d.prompt_sha256, d.params FROM extraction_document d JOIN record r ON r.document_id = d.id
           WHERE r.dataset_id = %s::uuid AND d.schema_id = %s""", (dataset_id, schema_id)).fetchall()
    hashes = {(h, ps.canonical_json(p if isinstance(p, dict) else {})) for h, p in docs if h}
    if hashes:
        for rel in cands:
            if all(hashlib.sha256(ps.render_prompt(rel["spec"], json.loads(pj)).encode("utf-8")).hexdigest() == h
                   for h, pj in hashes):
                return {"basis": "exact", "release": rel["number"], "candidates": [c["number"] for c in cands]}
    return {"basis": "contract", "release": cands[-1]["number"] if len(cands) == 1 else None, "candidates": [c["number"] for c in cands]}


def _credibility(conn: psycopg.Connection, d: dict) -> dict:
    """The badge the catalogue shows: computed here, or as published for a GitHub-synced copy."""
    cred = records.dataset_credibility(conn, d["id"])
    pm = d.get("published_meta") or {}
    if d.get("github_source") and isinstance(pm.get("credibility"), dict):
        cred = {**cred, **{k: v for k, v in pm["credibility"].items() if k != "dataset_id"}}
    return cred


def datasets_for(conn: psycopg.Connection, pid: str, spec: dict) -> list[dict]:
    live_sid = ps.schema_id(spec)
    out = []
    for d in _public_datasets(conn, bases_for(pid)):
        cred = _credibility(conn, d)
        for v in d["versions"]:
            v.update(_version_basis(conn, pid, d["id"], v["schema_id"], live_sid))
        rels = [{"number": r["number"], "created_at": r["created_at"], "credibility": r.get("credibility"),
                 "doi": r["doi"] if _counts(r.get("doi")) else None} for r in releases.list_for_dataset(conn, d["id"])]
        out.append({"id": d["id"], "slug": d["slug"], "title": d["title"], "description": d["description"],
                    "github_source": d["github_source"], "published_url": d["published_url"], "created_at": d["created_at"],
                    "credibility": {k: cred.get(k) for k in ("tier", "label", "n_records", "audited", "audited_pct", "agreement", "agreement_ci", "agreed")},
                    "n_records": d["n_records"], "n_papers": d["n_papers"], "versions": d["versions"], "releases": rels})
    return out


def _counts(doi: str | None) -> bool:
    from . import zenodo
    return zenodo.counts_here(doi)


def models_for(conn: psycopg.Connection, bases: list[str], evaluated: list[dict]) -> list[dict]:
    """The models that produced records with this preset on public datasets (model, mode, papers,
    records), with the ones the card names as its verification models marked — listed even
    before any public dataset exists."""
    rows = conn.execute(
        """SELECT coalesce(r.extraction->>'resolved_model', r.extraction->>'model', r.extraction->>'produced_by', 'unknown'),
                  r.extraction->>'produced_by', count(DISTINCT r.paper_id), count(*)
           FROM record r JOIN dataset d ON d.id = r.dataset_id
           WHERE d.visibility = 'public' AND d.publish_status = 'published' AND d.catalogue
             AND split_part(r.schema_id, '@', 1) = ANY(%s::text[]) AND NOT COALESCE(r.screened_empty, false)
           GROUP BY 1, 2 ORDER BY 4 DESC""", (bases,)).fetchall()
    def matches(name: str, e: dict) -> bool:
        a, b = (name or "").lower(), (e.get("model") or "").lower()
        return bool(a and b) and (a == b or a.startswith(b) or b.startswith(a) or b in a)
    out = []
    for name, produced_by, n_papers, n_records in rows:
        mode = "import" if produced_by else "extraction"
        hit = next((e for e in evaluated if matches(name, e)), None)
        out.append({"model": name, "mode": (hit or {}).get("mode") or mode, "produced_by": produced_by,
                    "n_papers": int(n_papers), "n_records": int(n_records), "verification": bool(hit), "note": (hit or {}).get("note")})
    seen = {o["model"].lower() for o in out}
    for e in evaluated:                               # named on the card but not (yet) seen in public data
        if not any(matches(s, e) for s in seen):
            out.append({"model": e["model"], "mode": e.get("mode") or "extraction", "produced_by": None,
                        "n_papers": None, "n_records": None, "verification": True, "note": e.get("note")})
    return out


def dashboards_for(conn: psycopg.Connection, dataset_ids: set[str]) -> dict:
    """Published dashboards over those datasets — Metalens-built and approved external ones."""
    if not dataset_ids:
        return {"internal": [], "external": []}
    internal = [{"id": x["id"], "title": x["title"], "dataset_id": x["dataset_id"], "dataset_title": x.get("dataset_title"),
                 "release": x.get("release"), "author": x.get("author"), "url": f"/dashboard?id={x['id']}"}
                for x in dashboards.list_public(conn) if x["dataset_id"] in dataset_ids]
    external = [{"id": x["id"], "title": x["title"], "dataset_id": x["dataset_id"], "dataset_title": x.get("dataset_title"),
                 "release": x.get("release_shown"), "author": x.get("authors"), "url": x["url"], "repo_url": x.get("repo_url")}
                for x in external_dashboards.list_public(conn) if x["dataset_id"] in dataset_ids]
    return {"internal": internal, "external": external}


def rollup(datasets: list[dict]) -> dict:
    """The verification figures across every linked public dataset, under the dataset badge rule."""
    n_records = sum(d["n_records"] for d in datasets)
    n_papers = sum(d["n_papers"] for d in datasets)
    audited = sum(int((d["credibility"] or {}).get("audited") or 0) for d in datasets)
    agreed = 0
    for d in datasets:
        c = d["credibility"] or {}
        if c.get("agreed") is not None:
            agreed += int(c["agreed"])
        elif c.get("agreement") is not None and c.get("audited"):
            agreed += round(float(c["agreement"]) * int(c["audited"]))
    badge = records._badge_from_counts(n_records, audited, agreed)   # noqa: SLF001
    return {"n_datasets": len(datasets), "n_records": n_records, "n_papers": n_papers, "audited": audited, "agreed": agreed,
            "disagreed": max(0, audited - agreed), **{k: badge[k] for k in ("tier", "audited_pct", "agreement", "agreement_ci", "label")}}


# ── the payloads ───────────────────────────────────────────────────────────────

def _release_summary(conn: psycopg.Connection, pid: str, spec: dict) -> dict:
    rels = preset_releases.list_for_preset(conn, pid)
    latest = rels[0] if rels else None
    concept = preset_releases.concept_doi(conn, pid)
    url = card_url(pid)
    return {"releases": [{**preset_releases.public_row(r), "bundle_url": f"/api/presets/{pid}/releases/{r['number']}/bundle"} for r in rels],
            "latest": preset_releases.public_row(latest) if latest else None,
            "concept_doi": concept,
            "cite_as": preset_releases.citation(spec, latest, concept_doi=concept, url=url),
            "cite_this_version": preset_releases.citation_for_version(spec, latest, url=url) if latest else None,
            "changed_since_release": preset_releases.changed_since(spec, latest) if latest else None}


def inventory(conn: psycopg.Connection, brand: brands.Brand | None = None) -> list[dict]:
    """Every card, with counts of what was built with it: one query over the public catalogue."""
    cards = card_presets(brand)
    if not cards:
        return []
    rows = conn.execute(
        """SELECT split_part(r.schema_id, '@', 1), r.dataset_id::text,
                  count(*) FILTER (WHERE NOT COALESCE(r.screened_empty, false)),
                  count(DISTINCT r.paper_id) FILTER (WHERE NOT COALESCE(r.screened_empty, false))
           FROM record r JOIN dataset d ON d.id = r.dataset_id
           WHERE d.visibility = 'public' AND d.publish_status = 'published' AND d.catalogue
           GROUP BY 1, 2""").fetchall()
    by_card: dict[str, dict] = {pid: {"datasets": set(), "records": 0, "papers": 0} for pid in cards}
    owner = {base: pid for pid in cards for base in bases_for(pid)}
    for base, did, nrec, npap in rows:
        pid = owner.get(base)
        if pid:
            by_card[pid]["datasets"].add(did); by_card[pid]["records"] += int(nrec or 0); by_card[pid]["papers"] += int(npap or 0)
    all_ds = {did for c in by_card.values() for did in c["datasets"]}
    dash_by_ds: dict[str, int] = {}
    if all_ds:
        for x in dashboards.list_public(conn) + external_dashboards.list_public(conn):
            if x["dataset_id"] in all_ds:
                dash_by_ds[x["dataset_id"]] = dash_by_ds.get(x["dataset_id"], 0) + 1
    out = []
    for pid, spec in cards.items():
        meta = spec["meta"]
        c = by_card[pid]
        out.append({"id": pid, "title": meta.get("title"), "tagline": meta.get("tagline"), "description": meta.get("description"),
                    "mode": meta.get("mode"), "hidden": bool(meta.get("hidden")), "brands": list(meta.get("brands") or []),
                    "authors": list(meta.get("authors") or []), "keywords": list(meta.get("keywords") or []), "license": meta.get("license"),
                    "schema_id": ps.schema_id(spec), "entries_label": (spec.get("entries") or {}).get("label"),
                    **_release_summary(conn, pid, spec),
                    "counts": {"datasets": len(c["datasets"]), "records": c["records"], "papers": c["papers"],
                               "dashboards": sum(dash_by_ds.get(did, 0) for did in c["datasets"])}})
    released = lambda x: 0 if x["latest"] else 1   # noqa: E731
    out.sort(key=lambda x: (released(x), (x["title"] or "").lower()))
    return out


def card(conn: psycopg.Connection, preset_id: str, *, viewer_is_admin: bool = False, user: dict | None = None) -> dict | None:
    """Everything the card page shows for one curated preset, or None for anything else (an
    unknown id, a preset without a card, a personal preset). The brand does not gate this: a
    DOI landing must always resolve."""
    pid = presets.resolve_id(preset_id)
    spec = card_presets().get(pid)
    if spec is None:
        return None
    meta = spec["meta"]
    cm = ps.card_meta(spec)
    datasets = datasets_for(conn, pid, spec)
    ds_ids = {d["id"] for d in datasets}
    dash = dashboards_for(conn, ds_ids)
    n_dash = {}
    for x in dash["internal"] + dash["external"]:
        n_dash[x["dataset_id"]] = n_dash.get(x["dataset_id"], 0) + 1
    for d in datasets:
        d["n_dashboards"] = n_dash.get(d["id"], 0)
    out = {"id": pid, "title": meta.get("title"), "tagline": meta.get("tagline"), "description": meta.get("description"),
           "mode": meta.get("mode"), "hidden": bool(meta.get("hidden")), "brands": list(meta.get("brands") or []),
           "authors": cm["authors"], "keywords": cm["keywords"], "license": cm["license"], "related": cm["related"],
           "card_md": cm["text"], "fields_doc": ps.field_docs(spec), "output_schema": ps.derive_output_schema(spec),
           "prompt": ps.render_prompt(spec) if meta.get("mode") != "import" else None,
           "schema_id": ps.schema_id(spec), "card_url": card_url(pid),
           **_release_summary(conn, pid, spec),
           "datasets": datasets, "dashboards": dash,
           "papers": {"related": [r for r in cm["related"] if r.get("kind") in ("paper", "preregistration")],
                      "n_primary": sum(d["n_papers"] for d in datasets), "catalogue_url": f"/catalog?preset={pid}"},
           "verification": rollup(datasets),
           "models": models_for(conn, bases_for(pid), cm["evaluated_with"]),
           "viewer": {"is_admin": bool(viewer_is_admin)}}
    if viewer_is_admin:
        from . import zenodo
        out["zenodo"] = zenodo.status(conn, user)
    return out


def dataset_dois_for(conn: psycopg.Connection, pid: str, spec: dict) -> list[str]:
    """The DOIs of public dataset releases built with the preset (for the deposit's relations)."""
    return [r["doi"] for d in datasets_for(conn, pid, spec) for r in d["releases"] if r.get("doi")]
