"""DOI-keyed metadata enrichment (plan §3.5).

Pipeline order, all free/CC0-or-open and safe to store:

    Crossref   -> canonical biblio + license + references + relation links + funders
    Unpaywall  -> OA status + best OA-PDF url
    OpenAlex   -> topics + keywords + cross-fill of any missing biblio
    JEL        -> predicted economics codes (no free DOI->JEL API exists)

Every produced field is a ``FieldValue`` carrying provenance (source/method/
confidence) so the UI can show EXTRACTED vs ENRICHED vs PREDICTED per field —
the differentiator. HTTP is injected (an ``httpx.Client``) so the whole pipeline
is testable offline with a MockTransport; nothing here touches the read path.

This is the body of the Arq ``enrich_paper`` task; until Redis/Arq is wired it is
called synchronously. Per plan: never enrich on the read path — persist results
and re-enrich on a ``last_enriched_at`` schedule.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .ingest import _normalize_doi


def contact_email() -> str:
    """Polite-pool / Unpaywall identity. Unpaywall *requires* an email."""
    return os.environ.get("PAPERLENS_CONTACT_EMAIL", "paperlens@example.org")


@dataclass
class FieldValue:
    value: Any
    source: str       # crossref | unpaywall | openalex | heuristic
    method: str       # api | predicted
    confidence: float = 1.0


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get_json(client: httpx.Client, url: str, params: dict | None = None) -> dict | None:
    try:
        r = client.get(url, params=params, timeout=20.0,
                       headers={"User-Agent": f"PaperLens/0.1 (mailto:{contact_email()})"})
        if r.status_code != 200:
            return None
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


# ── source: Crossref ──────────────────────────────────────────────────────────

def from_crossref(doi: str, client: httpx.Client) -> dict[str, FieldValue]:
    data = _get_json(client, f"https://api.crossref.org/works/{doi}",
                     params={"mailto": contact_email()})
    if not data or "message" not in data:
        return {}
    m = data["message"]
    out: dict[str, FieldValue] = {}

    def put(field: str, value: Any, conf: float = 1.0) -> None:
        if value not in (None, "", [], {}):
            out[field] = FieldValue(value, "crossref", "api", conf)

    title = m.get("title") or []
    put("title", title[0] if title else None)
    container = m.get("container-title") or []
    put("journal", container[0] if container else None)
    put("publisher", m.get("publisher"))
    put("work_type", m.get("type"))
    put("issn", m.get("ISSN"))

    authors = []
    for a in m.get("author") or []:
        authors.append({k: v for k, v in {
            "given": a.get("given"), "family": a.get("family"),
            "sequence": a.get("sequence"), "orcid": a.get("ORCID"),
            "affiliation": [af.get("name") for af in a.get("affiliation") or []] or None,
        }.items() if v is not None})
    put("authors", authors or None)

    issued = (m.get("issued") or {}).get("date-parts") or []
    if issued and issued[0]:
        parts = issued[0]
        put("year", parts[0] if parts else None)
        if len(parts) >= 3:
            put("publication_date", f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}")

    lic = m.get("license") or []
    if lic:
        put("license", lic[0].get("URL"))

    refs = [r.get("DOI") for r in m.get("reference") or [] if r.get("DOI")]
    put("referenced_works", refs or None)

    funders = [{"name": f.get("name"), "doi": f.get("DOI")} for f in m.get("funder") or []]
    put("funders", funders or None)

    # relation -> code/data/supplementary links (DOI-prefix typed)
    links = _links_from_relation(m.get("relation") or {})
    for k, v in links.items():
        if v:
            out[k] = FieldValue(v, "crossref", "api", 1.0)
    return out


def _links_from_relation(relation: dict) -> dict[str, list]:
    code, data, supp = [], [], []
    for rel_type, items in relation.items():
        for it in items if isinstance(items, list) else [items]:
            idv = it.get("id") if isinstance(it, dict) else None
            if not idv:
                continue
            bucket = _classify_doi(idv)
            entry = {"id": idv, "relation": rel_type}
            (code if bucket == "code" else data if bucket == "data" else supp).append(entry)
    return {"code_links": code, "data_links": data, "supplementary_links": supp}


# DOI-prefix -> artifact type (plan §3.5 link strategy)
_PREFIX_BUCKET = {
    "10.5061": "data",   # Dryad
    "10.6084": "data",   # figshare
    "10.7910": "data",   # Harvard Dataverse
    "10.3886": "data",   # openICPSR
    "10.5281": "code",   # Zenodo (often archived code; data otherwise — default code)
}


def _classify_doi(idv: str) -> str:
    d = _normalize_doi(idv)
    for prefix, bucket in _PREFIX_BUCKET.items():
        if d.startswith(prefix + "/"):
            return bucket
    return "supplementary"


# ── source: Unpaywall ─────────────────────────────────────────────────────────

def from_unpaywall(doi: str, client: httpx.Client) -> dict[str, FieldValue]:
    data = _get_json(client, f"https://api.unpaywall.org/v2/{doi}",
                     params={"email": contact_email()})
    if not data:
        return {}
    out: dict[str, FieldValue] = {}
    if data.get("is_oa") is not None:
        out["is_oa"] = FieldValue(bool(data["is_oa"]), "unpaywall", "api")
    if data.get("oa_status"):
        out["oa_status"] = FieldValue(data["oa_status"], "unpaywall", "api")
    best = data.get("best_oa_location") or {}
    pdf = best.get("url_for_pdf") or best.get("url")
    if pdf:
        out["oa_pdf_url"] = FieldValue(pdf, "unpaywall", "api")
    return out


# ── source: OpenAlex ──────────────────────────────────────────────────────────

def from_openalex(doi: str, client: httpx.Client) -> dict[str, FieldValue]:
    data = _get_json(
        client, f"https://api.openalex.org/works/doi:{doi}",
        params={"mailto": contact_email(),
                "select": "id,doi,primary_topic,topics,keywords,primary_location,"
                          "publication_year,open_access"},
    )
    if not data:
        return {}
    out: dict[str, FieldValue] = {}

    if data.get("id"):
        out["openalex_id"] = FieldValue(data["id"], "openalex", "api")

    pt = data.get("primary_topic") or {}
    if pt.get("display_name"):
        out["primary_topic"] = FieldValue(pt["display_name"], "openalex", "api",
                                          float(pt.get("score") or 1.0))
    topics = []
    for t in data.get("topics") or []:
        topics.append({
            "id": t.get("id"), "display_name": t.get("display_name"),
            "score": t.get("score"),
            "subfield": (t.get("subfield") or {}).get("display_name"),
            "field": (t.get("field") or {}).get("display_name"),
            "domain": (t.get("domain") or {}).get("display_name"),
        })
    if topics:
        out["openalex_topics"] = FieldValue(topics, "openalex", "api")

    kws = [k.get("display_name") for k in data.get("keywords") or [] if k.get("display_name")]
    if kws:
        out["author_keywords"] = FieldValue(kws, "openalex", "api")

    # cross-fill biblio that may be missing from Crossref
    loc = (data.get("primary_location") or {}).get("source") or {}
    if loc.get("display_name"):
        out["journal"] = FieldValue(loc["display_name"], "openalex", "api")
    if data.get("publication_year"):
        out["year"] = FieldValue(data["publication_year"], "openalex", "api")
    oa = data.get("open_access") or {}
    if oa.get("is_oa") is not None:
        out["is_oa"] = FieldValue(bool(oa["is_oa"]), "openalex", "api")
    if oa.get("oa_url"):
        out["oa_pdf_url"] = FieldValue(oa["oa_url"], "openalex", "api")
    return out


# ── JEL prediction (no free DOI->JEL API; predict from title+abstract) ─────────

# Minimal keyword->JEL map for the AI-and-labour beachhead. The real predictor is
# an LLM given the JEL tree in-context; this offline heuristic is the testable
# baseline. All outputs are flagged method=predicted, confidence<1.
_JEL_RULES: list[tuple[str, str, str]] = [
    (r"\b(human capital|skill|training|education)\b", "J24", "Human Capital; Skills"),
    (r"\b(wage|earnings|salary|pay)\b", "J31", "Wage Level and Structure"),
    (r"\b(employment|labou?r supply|labou?r demand|jobs?)\b", "J21", "Labor Force / Employment"),
    (r"\b(automation|technological change|robot|artificial intelligence|\bAI\b|machine learning)\b",
     "O33", "Technological Change: Choices and Consequences"),
    (r"\b(neural network|deep learning)\b", "C45", "Neural Networks and Related Topics"),
    (r"\b(meta-?analysis|meta-?regression)\b", "C83", "Survey Methods; Meta-analysis"),
]

JelPredictor = Callable[[str, str], list[FieldValue]]


def predict_jel_heuristic(title: str | None, abstract: str | None) -> list[FieldValue]:
    text = f"{title or ''} {abstract or ''}".lower()
    hits: list[FieldValue] = []
    seen: set[str] = set()
    for pattern, code, _label in _JEL_RULES:
        if re.search(pattern, text, flags=re.IGNORECASE) and code not in seen:
            seen.add(code)
            # 2-digit confidence higher than the 3-digit leaf (per research).
            hits.append(FieldValue(code, "heuristic", "predicted", 0.6))
    return hits


# ── orchestrator ──────────────────────────────────────────────────────────────

def enrich_fields(doi: str, *, client: httpx.Client,
                  abstract: str | None = None, title_hint: str | None = None,
                  jel_predictor: JelPredictor | None = None) -> dict[str, FieldValue]:
    """Run the pipeline and merge to one field map (provenance preserved).

    Merge rule: process sources in order; a later source only fills a field that
    an earlier (more authoritative) source left empty (cross-fill) — EXCEPT
    OA-PDF, where Unpaywall is authoritative and wins.
    """
    doi = _normalize_doi(doi)
    merged: dict[str, FieldValue] = {}

    def merge(new: dict[str, FieldValue], *, override: set[str] = frozenset()) -> None:
        for k, v in new.items():
            if k not in merged or k in override:
                merged[k] = v

    merge(from_crossref(doi, client))
    merge(from_unpaywall(doi, client), override={"is_oa", "oa_status", "oa_pdf_url"})
    merge(from_openalex(doi, client))  # cross-fill only (no override)

    title = (merged.get("title").value if "title" in merged else title_hint)
    predictor = jel_predictor or predict_jel_heuristic
    jel = predictor(title, abstract)
    if jel:
        merged["jel_codes"] = FieldValue([fv.value for fv in jel],
                                         jel[0].source, jel[0].method, jel[0].confidence)
    return merged


def enrich_paper(conn, doi: str, *, client: httpx.Client | None = None,
                 abstract: str | None = None, title_hint: str | None = None,
                 jel_predictor: JelPredictor | None = None) -> dict[str, Any]:
    """The Arq ``enrich_paper`` task body: enrich a DOI and persist columns +
    provenance. Best-effort and idempotent — safe to re-run on a schedule.
    """
    from . import records

    norm = _normalize_doi(doi)
    close = client is None
    client = client or httpx.Client()
    try:
        fields_fv = enrich_fields(norm, client=client, abstract=abstract,
                                  title_hint=title_hint, jel_predictor=jel_predictor)
    finally:
        if close:
            client.close()

    field_values = {k: v.value for k, v in fields_fv.items()}
    provenance = [(k, v.source, v.method, v.confidence) for k, v in fields_fv.items()]
    # One transaction so the paper upsert + enrichment commit atomically and
    # durably — critical in the Arq worker, where the connection is closed (and
    # would otherwise roll back) as soon as the task returns.
    with conn.transaction():
        paper_id = records.get_or_create_paper_by_doi(conn, norm)
        records.update_paper_enrichment(conn, paper_id, field_values, provenance)
    return {"paper_id": paper_id, "doi": norm,
            "fields": sorted(field_values), "provenance_count": len(provenance)}
