"""The files a workflow release is deposited as — and downloaded as from the card page: a
loadable workflow directory (``workflow.json`` with ``card.md`` and any derive prompt beside it,
the shape ``workflow_spec.load_dir`` reads) plus what a reader of the deposit needs:
``steps.md`` (every step documented from the declaration), ``CITATION.cff`` and a README naming
the DOI, the contract, the presets and the engine."""
from __future__ import annotations

import copy
import json

from . import preset_bundle, release_export
from . import preset_spec as ps
from . import workflow_spec as ws

ENGINE_REPO = preset_bundle.ENGINE_REPO
_yaml = preset_bundle._yaml   # noqa: SLF001


def _review_text(rv: dict | None) -> str:
    if not rv or rv.get("mode", "none") == "none":
        return "no review declared"
    if rv["mode"] == "all":
        return f"every record reviewed before the {'next step' if rv.get('blocks') == 'next' else 'release'}"
    size = f"{rv['n']} records" if rv.get("n") is not None else f"a {rv.get('share')} share"
    strat = f", stratified by {', '.join(rv['stratify'])}" if rv.get("stratify") else ""
    unit = " (whole papers)" if rv.get("unit") == "paper" else ""
    return f"a random sample of {size}{unit}{strat}, reviewed before the {'next step' if rv.get('blocks') == 'next' else 'release'}"


def steps_markdown(spec: dict, presets: list[dict] | None = None) -> str:
    """One section per step: what it is, what it reads, what it pins, how it is reviewed."""
    from . import checks
    by_pin = {p["schema_id"]: p for p in (presets or [])}
    lines = [f"# Steps — {(spec.get('meta') or {}).get('title') or spec['id']} (`{ws.workflow_id(spec)}`)", ""]
    for k, st in enumerate(spec.get("steps") or [], start=1):
        kind = st["kind"]
        lines += [f"## {k}. {st.get('label') or st['id']} (`{st['id']}`, {kind})", ""]
        if st.get("help"):
            lines += [st["help"], ""]
        if kind in ("extract", "agent"):
            pin = st.get("preset")
            p = by_pin.get(pin) or {}
            if st.get("dataset"):
                lines.append(f"- dataset role: `{st['dataset']}` (the group's dataset of that role)")
            lines.append(f"- preset: `{pin}`" + (f" — preset release v{p['preset_release']}" if p.get("preset_release") else " — no preset release matches this contract")
                         + (f", concept DOI {p['concept_doi']}" if p.get("concept_doi") else ""))
            lines.append(f"- source: {'the preset runs on the worker, one model call per paper' if st.get('source') == 'live' else 'results made outside Metalens are uploaded into the run'}")
            if (st.get("k") or 1) > 1:
                lines.append(f"- runs: {st['k']} per paper, merged by a vote on {', '.join((st.get('vote') or {}).get('identity') or [])}")
            if (st.get("input") or {}).get("previous"):
                lines.append(f"- reads: {', '.join(st['input']['previous'])}")
        elif kind == "check":
            reg = checks.REGISTRY.get(st.get("check")) or {}
            lines.append(f"- check: `{st.get('check')}` over {', '.join(st.get('over') or [])} — {reg.get('label', '')}")
            lines.append(f"- on a finding at or above `{st.get('severity', 'error')}`: {st.get('on_fail', 'note')}")
            if st.get("params"):
                lines.append(f"- parameters: `{json.dumps(st['params'], ensure_ascii=False)}`")
            codes = reg.get("codes") or {}
            if codes:
                lines.append("- codes: " + ", ".join(f"`{c}` ({sev})" for c, sev in codes.items()))
        elif kind == "derive":
            lines.append(f"- over: {st.get('over')} · identity: {', '.join(st.get('identity') or [])} · calls: "
                         + ", ".join(f"{c.get('id')} ({c.get('kind')})" for c in st.get("calls") or [] if isinstance(c, dict)))
        elif kind == "vocabulary":
            lines.append(f"- vocabulary of `{st.get('column')}` ({st.get('unit', 'entries')})")
        elif kind == "pages":
            lines.append(f"- pages of group `{st.get('group')}`, keyed by `{st.get('keyed_by')}`")
        elif kind == "crosscheck":
            lines.append("- the claims-vs-tables check against the dataset's companion")
        elif kind == "review":
            lines.append(f"- a joint review stage over {', '.join(st.get('over') or [])}: each drawn paper's documents of those steps, in one worklist")
        elif kind == "release":
            lines.append(f"- gate: {', '.join(st.get('gate') or []) or 'none'} — a release holds while a gate step is stale or awaits review")
        if kind in ws.REVIEWED_KINDS:
            lines.append(f"- review: {_review_text(st.get('review'))}")
        lines.append("")
    return "\n".join(lines)


def citation_cff(spec: dict, release: dict, *, doi: str | None, concept_doi: str | None, card_url: str | None) -> str:
    meta = spec.get("meta") or {}
    cm = ps.card_meta(spec)
    lines = ["cff-version: 1.2.0", f"message: {_yaml('If you use this workflow, please cite it as below.')}", "type: software",
             "title: " + _yaml(f"{meta.get('title') or spec['id']}: Metalens workflow"),
             f"version: v{release['number']}", f"date-released: {str(release.get('created_at') or '')[:10]}"]
    if doi:
        lines.append(f"doi: {_yaml(doi)}")
    if cm.get("license"):
        lines.append(f"license: {cm['license']}")
    if card_url:
        lines.append(f"url: {_yaml(card_url)}")
    lines.append(f"repository-code: {_yaml(ENGINE_REPO)}")
    if meta.get("tagline"):
        lines.append(f"abstract: {_yaml(meta['tagline'])}")
    lines.append("authors:")
    for a in cm["authors"]:
        lines.append(f"  - family-names: {_yaml(a.get('family') or '')}")
        if a.get("given"):
            lines.append(f"    given-names: {_yaml(a['given'])}")
        if a.get("orcid"):
            lines.append(f"    orcid: {_yaml('https://orcid.org/' + a['orcid'])}")
        if a.get("affiliation"):
            lines.append(f"    affiliation: {_yaml(a['affiliation'])}")
    if cm["keywords"]:
        lines.append("keywords:")
        lines += [f"  - {_yaml(k)}" for k in cm["keywords"]]
    ids = []
    if concept_doi:
        ids.append((concept_doi, "The concept DOI: resolves to the newest version of this workflow."))
    for p in release.get("presets") or []:
        if p.get("concept_doi"):
            ids.append((p["concept_doi"], f"The preset the step '{p['step']}' runs ({p['schema_id']})."))
    if ids:
        lines.append("identifiers:")
        for value, desc in ids:
            lines += ["  - type: doi", f"    value: {_yaml(value)}", f"    description: {_yaml(desc)}"]
    return "\n".join(lines) + "\n"


def readme(spec: dict, release: dict, *, doi: str | None, concept_doi: str | None, card_url: str | None) -> str:
    from . import workflow_releases
    meta = spec.get("meta") or {}
    cm = ps.card_meta(spec)
    eng = release.get("engine") or {}
    head = (f"Frozen on {str(release.get('created_at') or '')[:10]} · contract `{release['contract']}` · spec sha256 `{release['spec_sha']}` · "
            f"engine {eng.get('name') or 'metalens'} {eng.get('version') or ''}" + (f" ({eng.get('git_sha')[:8]})" if eng.get("git_sha") else ""))
    if doi:
        head += f" · DOI [{doi}](https://doi.org/{doi})"
    url = card_url or ""
    lines = [f"# {meta.get('title') or spec['id']} — workflow release v{release['number']}", "", head, ""]
    if meta.get("description"):
        lines += ["## What this is", "", meta["description"], ""]
    if release.get("notes"):
        lines += ["## Release notes", "", release["notes"], ""]
    lines += ["## How to cite", "", "This version:", "", "> " + workflow_releases.citation(spec, release, concept_doi=doi, url=url), ""]
    if concept_doi:
        lines += ["Any version (the concept DOI follows the newest):", "", "> " + workflow_releases.citation(spec, release, concept_doi=concept_doi, url=url), ""]
    lines += ["## Presets", ""]
    for p in release.get("presets") or []:
        lines.append(f"- step `{p['step']}`: `{p['schema_id']}`" + (f" — preset release v{p['preset_release']}" if p.get("preset_release") else "")
                     + (f", https://doi.org/{p['concept_doi']}" if p.get("concept_doi") else ""))
    lines += ["", "## Files", "",
              ("- `workflow.json` — the declaration (Metalens workflow format 1) as released; put it in a folder named by "
               "`PAPERLENS_WORKFLOW_DIRS` and a dataset can declare it"),
              "- `steps.md` — every step documented: what it reads, what it pins, how it is checked and reviewed",
              *(["- `card.md` — the card"] if cm.get("text") else []),
              "- `CITATION.cff` — citation metadata", ""]
    if cm["related"]:
        lines += ["## Related", ""]
        for r in cm["related"]:
            where = f" https://doi.org/{r['doi']}" if r.get("doi") else (f" {r['url']}" if r.get("url") else "")
            lines.append(f"- {r['kind']}: {r['title']}{where}")
        lines.append("")
    lines += [f"Licence: {cm.get('license') or 'see the card'}. Made with Metalens ({ENGINE_REPO}).", ""]
    return "\n".join(lines)


def build(spec: dict, release: dict, *, doi: str | None = None, concept_doi: str | None = None, card_url: str | None = None) -> dict[str, bytes]:
    """{relative path: bytes} for one release — a loadable workflow directory plus its papers."""
    out = copy.deepcopy(spec)
    out.pop("implicit", None)
    files: dict[str, str] = {}
    for st in out.get("steps") or []:
        if st.get("kind") == "derive":
            for call in st.get("calls") or []:
                text = (call.get("prompt") or {}).pop("text", None) if isinstance(call.get("prompt"), dict) else None
                if text:
                    name = f"{st['id']}.{call.get('id') or 'call'}.md"
                    files[name] = text
                    call["prompt"] = {"file": name}
    card_text = (out.get("card") or {}).get("text")
    if card_text:
        files["card.md"] = preset_bundle.for_deposit(card_text, card_url=card_url)
        out["card"] = {"file": "card.md"}
    files["workflow.json"] = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    files["steps.md"] = steps_markdown(spec, release.get("presets"))
    files["CITATION.cff"] = citation_cff(spec, release, doi=doi, concept_doi=concept_doi, card_url=card_url)
    files["README.md"] = readme(spec, release, doi=doi, concept_doi=concept_doi, card_url=card_url)
    return {k: v.encode("utf-8") for k, v in files.items()}


def as_zip(files: dict[str, bytes], workflow_id: str, number: int) -> bytes:
    return release_export.as_zip(files, f"{workflow_id}-v{number}")
