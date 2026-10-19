"""A DOI for a dataset release — and for a preset release — minted on Zenodo.

One Zenodo record per dataset (or per curated preset); every release deposited becomes a new
*version* of it, so a release has its own DOI (citable, frozen) and the lineage a concept DOI that
always resolves to the newest version. For a dataset the files are the release's static export
(release.json, the tables, the evidence, a README) — the same files the datasets repository holds;
for a preset they are the bundle (preset.json, prompt.md, card.md, fields.md, CITATION.cff, README).
Either way the files name the DOI they were given, because Zenodo reserves the DOI before the
files are uploaded. ``deposit_files`` is the one exchange; the two ``deposit*`` functions feed it.

Configuration (environment)
  PAPERLENS_ZENODO_TOKEN     personal access token with ``deposit:write`` and ``deposit:actions``
  PAPERLENS_ZENODO_SANDBOX   truthy → sandbox.zenodo.org (test DOIs that resolve nowhere)

  PAPERLENS_ZENODO_USERS     optional: comma-separated account e-mails allowed to mint; unset → any account
  PAPERLENS_ZENODO_PER_DAY   deposits one account may make per 24 h (default 5)

Minting is opt-in per release (a DOI is permanent) and every DOI lands under the token's Zenodo
account, so it is guarded: a signed-in owner who owns every record, a release with at least one
paper, the allow-list when set, and the daily cap. Nothing here is queued: the files are small
and the whole exchange takes a few seconds.
"""
from __future__ import annotations

import html
import os

import httpx

from . import records, release_export, releases

_PROD = "https://zenodo.org/api"
_SANDBOX = "https://sandbox.zenodo.org/api"


def token() -> str | None:
    return os.environ.get("PAPERLENS_ZENODO_TOKEN") or None


def sandbox() -> bool:
    return (os.environ.get("PAPERLENS_ZENODO_SANDBOX") or "").strip().lower() in ("1", "true", "yes", "on")


def configured() -> bool:
    return bool(token())


def allowed_users() -> set[str] | None:
    raw = os.environ.get("PAPERLENS_ZENODO_USERS") or ""
    users = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return users or None


def per_day() -> int:
    try:
        return max(1, int(os.environ.get("PAPERLENS_ZENODO_PER_DAY") or 5))
    except ValueError:
        return 5


def may_mint(conn, user: dict | None) -> str | None:
    """Why this account may not mint a DOI here — None when it may."""
    if not configured():
        return "Zenodo isn’t configured on this server."
    if not user:
        return "Sign in to mint a DOI: it is issued under the server’s Zenodo account and is permanent."
    allow = allowed_users()
    if allow is not None and (user.get("email") or "").lower() not in allow:
        return "This account isn’t allowed to mint DOIs on this server."
    n = conn.execute("SELECT count(*) FROM dataset_release r JOIN dataset d ON d.id = r.dataset_id "
                     "WHERE d.owner_user_id = %s::uuid AND r.zenodo_record_id IS NOT NULL AND r.doi_minted_at > now() - interval '1 day'",
                     (user["id"],)).fetchone()[0]
    n += conn.execute("SELECT count(*) FROM preset_release WHERE doi_minted_by = %s::uuid AND zenodo_record_id IS NOT NULL "
                      "AND doi_minted_at > now() - interval '1 day'", (user["id"],)).fetchone()[0]
    if n >= per_day():
        return f"This account minted {n} DOIs in the last 24 hours; the limit is {per_day()}."
    return None


def status(conn=None, user: dict | None = None) -> dict:
    """What the dataset page shows: whether a DOI can be minted here (and by this account), and
    whether it would be a test one."""
    out = {"configured": configured(), "sandbox": sandbox()}
    if conn is not None:
        why = may_mint(conn, user)
        out["allowed"], out["why_not"] = why is None, why
    return out


def base_url() -> str:
    return _SANDBOX if sandbox() else _PROD


def new_client() -> httpx.Client:
    """The HTTP client for one exchange (tests replace this)."""
    return httpx.Client(timeout=60.0)


def is_test_doi(doi: str | None) -> bool:
    """Sandbox DOIs carry the 10.5072 prefix; they resolve nowhere and never count as real."""
    return bool(doi) and doi.startswith("10.5072/")


def counts_here(doi: str | None) -> bool:
    """Does this DOI belong to the Zenodo this server talks to? A sandbox DOI is ignored once the
    server is on the real Zenodo (and vice versa), so a release can be minted again after the switch."""
    return bool(doi) and is_test_doi(doi) == sandbox()


def _headers() -> dict:
    return {"Authorization": f"Bearer {token()}", "Accept": "application/json"}


def _ok(r: httpx.Response, step: str) -> httpx.Response:
    """Zenodo explains a refusal in its JSON body; surface that instead of the bare status."""
    if r.is_success:
        return r
    try:
        body = r.json()
        msg = body.get("message") or "; ".join(f"{e.get('field')}: {e.get('message')}" for e in body.get("errors") or []) or str(body)[:300]
    except ValueError:
        msg = (r.text or "")[:300]
    raise RuntimeError(f"Zenodo {step}: HTTP {r.status_code} — {msg}")


def metadata(ds: dict, rel: dict, *, github_url: str | None) -> dict:
    """The record's metadata, from the publishing details the owner already fills in."""
    anonymous = ds.get("attribution") == "anonymous"
    name = (ds.get("owner_citation_name") or "").strip()
    changes = rel.get("changes") or {}
    what = []
    if changes.get("first"):
        what.append("First release.")
    else:
        for key, word in (("papers_added", "added"), ("papers_removed", "removed")):
            n = len(changes.get(key) or [])
            if n:
                what.append(f"{n} paper{'s' if n != 1 else ''} {word}.")
    desc = [html.escape(ds.get("description") or ""), html.escape(rel.get("notes") or ""), " ".join(what),
            f"Release v{rel['number']} of a Metalens dataset: {changes.get('n_papers') or (rel.get('stats') or {}).get('n_papers') or '?'} papers, "
            f"{(rel.get('credibility') or {}).get('label') or 'unverified'}. Every value is tied to the passage of the paper it was "
            f"read from (evidence.json); release.json describes the tables. Content sha256 {rel['content_sha']}."]
    related = []
    if github_url:
        related.append({"identifier": f"{github_url}/releases/v{rel['number']}", "relation": "isIdenticalTo", "scheme": "url"})
    meta = {"title": f"{ds.get('title') or ds.get('slug') or 'Dataset'} — release v{rel['number']}",
            "upload_type": "dataset",
            "description": "<p>" + "</p><p>".join(p for p in desc if p.strip()) + "</p>",
            "creators": [{"name": "Anonymous"}] if anonymous or not name else [{"name": name}],
            "version": f"v{rel['number']}",
            "publication_date": str(rel.get("created_at") or "")[:10] or None,
            "access_right": "open", "license": "cc-by-4.0",
            "keywords": [k for k in (ds.get("keywords") or []) if isinstance(k, str)] + ["Metalens"]}
    if related:
        meta["related_identifiers"] = related
    return {"metadata": {k: v for k, v in meta.items() if v is not None}}


def _prior(conn, dataset_id: str) -> dict | None:
    rows = conn.execute("SELECT zenodo_record_id, doi FROM dataset_release WHERE dataset_id = %s::uuid AND zenodo_record_id IS NOT NULL "
                        "ORDER BY number DESC", (dataset_id,)).fetchall()
    return next(({"record_id": r[0]} for r in rows if counts_here(r[1])), None)


def deposit_files(*, prior_record_id: int | None, metadata, files, client: httpx.Client | None = None) -> dict:
    """The one Zenodo exchange: a new deposition (or a new version of ``prior_record_id``, with the
    copied files dropped), the reserved DOI, the files ``files(doi)`` returns uploaded to the flat
    bucket, the metadata ``metadata(doi)`` returns, publish. Returns ``{doi, record_id, url,
    conceptdoi}``; raises RuntimeError when Zenodo refuses a step. Pure HTTP: callers persist."""
    base = base_url()
    close = client is None
    client = client or new_client()
    try:
        if prior_record_id:                           # the next version of the lineage's record
            r = _ok(client.post(f"{base}/deposit/depositions/{prior_record_id}/actions/newversion", headers=_headers()), "new version")
            draft_url = (r.json().get("links") or {}).get("latest_draft")
            if not draft_url:
                raise RuntimeError("Zenodo new version: no draft link in the reply.")
            draft = _ok(client.get(draft_url, headers=_headers()), "read draft").json()
            for f in draft.get("files") or []:        # the previous version's files come along; this release brings its own
                _ok(client.delete(f"{base}/deposit/depositions/{draft['id']}/files/{f['id']}", headers=_headers()), "drop copied file")
        else:
            draft = _ok(client.post(f"{base}/deposit/depositions", headers=_headers(), json={}), "create deposition").json()
        doi = ((draft.get("metadata") or {}).get("prereserve_doi") or {}).get("doi")
        bucket = (draft.get("links") or {}).get("bucket")
        if not doi or not bucket:
            raise RuntimeError("Zenodo deposition: no reserved DOI or file bucket in the reply.")
        for path, content in sorted(files(doi).items()):
            _ok(client.put(f"{bucket}/{uploaded_name(path)}", headers={**_headers(), "Content-Type": "application/octet-stream"}, content=content),
                f"upload {path}")
        _ok(client.put(f"{base}/deposit/depositions/{draft['id']}", headers=_headers(), json=metadata(doi)), "metadata")
        rec = _ok(client.post(f"{base}/deposit/depositions/{draft['id']}/actions/publish", headers=_headers()), "publish").json()
    finally:
        if close:
            client.close()
    doi = rec.get("doi") or doi
    links = rec.get("links") or {}
    url = links.get("record_html") or links.get("html") or f"https://doi.org/{doi}"     # the public record page, not the deposit form
    return {"doi": doi, "record_id": rec.get("id") or draft["id"], "url": url, "conceptdoi": rec.get("conceptdoi")}


def deposit(conn, release: dict, *, client: httpx.Client | None = None) -> dict:
    """Mint the DOI for one dataset release (a releases row; the snapshot is loaded here). Returns
    the release row with ``doi``, ``zenodo_record_id`` and ``zenodo_url`` filled in. Raises when
    Zenodo is not configured, the release already has a DOI, or Zenodo refuses a step."""
    if not configured():
        raise RuntimeError("Zenodo is not configured on this server (PAPERLENS_ZENODO_TOKEN).")
    if counts_here(release.get("doi")):
        raise RuntimeError(f"Release v{release['number']} already has a DOI: {release['doi']}.")
    ds = records.get_dataset(conn, release["dataset_id"]) or {}
    prior = _prior(conn, release["dataset_id"])
    full = releases.get(conn, release["id"], with_snapshot=True)

    def files(doi: str) -> dict[str, bytes]:
        return release_export.build(conn, {**full, "doi": doi})

    def meta(doi: str) -> dict:
        return metadata(ds, {**full, "doi": doi}, github_url=ds.get("published_url"))

    out = deposit_files(prior_record_id=prior["record_id"] if prior else None, metadata=meta, files=files, client=client)
    with conn.transaction():
        conn.execute("UPDATE dataset_release SET doi = %s, zenodo_record_id = %s, zenodo_url = %s, doi_minted_at = now() WHERE id = %s::uuid",
                     (out["doi"], out["record_id"], out["url"], release["id"]))
        if out.get("conceptdoi"):
            conn.execute("UPDATE dataset SET zenodo_concept_doi = %s WHERE id = %s::uuid", (out["conceptdoi"], release["dataset_id"]))
    return releases.get(conn, release["id"])


# ── preset releases ───────────────────────────────────────────────────────────

_RELATION_OF = {"paper": "isSupplementTo", "preregistration": "isSupplementTo", "supplement": "isSupplementedBy",
                "repository": "isPartOf", "dashboard": "isReferencedBy"}


def preset_metadata(spec: dict, rel: dict, *, card_url: str | None, dataset_dois: list[str] = (),
                    companion_dois: dict[str, str] | None = None) -> dict:
    """A preset release as a Zenodo record: software (a preset is an executable declaration run by
    an engine), its authors with ORCIDs, the card's licence and keywords, and relations to the
    papers, supplements, repositories and dashboards the card names, to the card page, and to the
    public dataset releases built with it."""
    from . import preset_spec as ps
    meta = spec.get("meta") or {}
    cm = ps.card_meta(spec)
    what = "import schema" if meta.get("mode") == "import" else "extraction preset"
    counts = ps.field_docs(spec)["counts"]
    desc = [html.escape(meta.get("description") or ""), html.escape(rel.get("notes") or ""),
            html.escape(f"Release v{rel['number']} of a Metalens {what}: {counts['fields']} fields"
                        + (f" and {counts['columns']} table columns" if counts["columns"] else "")
                        + f"; schema id {rel['schema_id']}; spec sha256 {rel['spec_sha']}. The deposit is the preset itself (preset.json, "
                        f"its prompt and card) with every field documented (fields.md); load it with Metalens to run the same extraction.")]
    creators = []
    for a in cm["authors"]:
        c = {"name": f"{a.get('family')}, {a.get('given')}" if a.get("given") else a.get("family")}
        if a.get("orcid"):
            c["orcid"] = a["orcid"]
        if a.get("affiliation"):
            c["affiliation"] = a["affiliation"]
        creators.append(c)
    related = []
    for r in cm["related"]:
        relation = _RELATION_OF.get(r.get("kind"))
        if r.get("kind") == "preset" and (companion_dois or {}).get(r.get("id")):
            related.append({"identifier": companion_dois[r["id"]], "relation": "references", "scheme": "doi"})
        elif relation and r.get("doi"):
            related.append({"identifier": r["doi"], "relation": relation, "scheme": "doi"})
        elif relation and r.get("url"):
            related.append({"identifier": r["url"], "relation": relation, "scheme": "url"})
    related.append({"identifier": "https://github.com/johanna-einsiedler/metalens", "relation": "isPartOf", "scheme": "url"})
    if card_url:
        related.append({"identifier": card_url, "relation": "isDocumentedBy", "scheme": "url"})
    related += [{"identifier": d, "relation": "isSourceOf", "scheme": "doi"} for d in dataset_dois if d]
    seen: set[tuple] = set()
    related = [x for x in related if not ((x["identifier"], x["relation"]) in seen or seen.add((x["identifier"], x["relation"])))]
    out = {"title": f"{meta.get('title') or spec['id']} — {what} v{rel['number']}",
           "upload_type": "software",
           "description": "<p>" + "</p><p>".join(p for p in desc if p.strip()) + "</p>",
           "creators": creators or [{"name": "Anonymous"}],
           "version": f"v{rel['number']}",
           "publication_date": str(rel.get("created_at") or "")[:10] or None,
           "access_right": "open", "license": (cm.get("license") or "CC-BY-4.0").lower(),
           "keywords": [k for k in cm["keywords"] if isinstance(k, str)] + ["Metalens", "extraction preset"],
           "related_identifiers": related}
    return {"metadata": {k: v for k, v in out.items() if v is not None}}


def _prior_preset(conn, preset_id: str) -> int | None:
    rows = conn.execute("SELECT zenodo_record_id, doi FROM preset_release WHERE preset_id = %s AND zenodo_record_id IS NOT NULL ORDER BY number DESC",
                        (preset_id,)).fetchall()
    return next((r[0] for r in rows if counts_here(r[1])), None)


def deposit_preset(conn, release: dict, *, client: httpx.Client | None = None, minted_by: str | None = None,
                   card_url: str | None = None, dataset_dois: list[str] = ()) -> dict:
    """Mint the DOI for one preset release (a preset_releases row). Returns the row with ``doi``,
    ``zenodo_record_id``, ``zenodo_url`` and ``zenodo_concept_doi`` filled in."""
    from . import preset_bundle, preset_releases
    if not configured():
        raise RuntimeError("Zenodo is not configured on this server (PAPERLENS_ZENODO_TOKEN).")
    if counts_here(release.get("doi")):
        raise RuntimeError(f"Release v{release['number']} already has a DOI: {release['doi']}.")
    spec = release["spec"]
    prior = _prior_preset(conn, release["preset_id"])
    concept = preset_releases.concept_doi(conn, release["preset_id"])
    companions = {r["id"]: preset_releases.concept_doi(conn, r["id"])
                  for r in (spec.get("meta") or {}).get("related") or [] if r.get("kind") == "preset" and r.get("id")}
    out = deposit_files(
        prior_record_id=prior,
        metadata=lambda doi: preset_metadata(spec, release, card_url=card_url, dataset_dois=list(dataset_dois),
                                             companion_dois={k: v for k, v in companions.items() if v}),
        files=lambda doi: preset_bundle.build(spec, release, doi=doi, concept_doi=concept, card_url=card_url),
        client=client)
    with conn.transaction():
        conn.execute("UPDATE preset_release SET doi = %s, zenodo_record_id = %s, zenodo_url = %s, zenodo_concept_doi = %s, "
                     "doi_minted_at = now(), doi_minted_by = %s::uuid WHERE id = %s::uuid",
                     (out["doi"], out["record_id"], out["url"], out.get("conceptdoi"),
                      minted_by if records._is_uuid(minted_by or "") else None, release["id"]))   # noqa: SLF001
    return preset_releases.get(conn, release["id"])


def uploaded_name(path: str) -> str:
    """Zenodo buckets are flat: tables/entries.json is stored as tables__entries.json."""
    return path.replace("/", "__")

