"""The files a preset release is deposited as — and downloaded as from the card page.

A bundle is a loadable preset directory (``preset.json`` with ``prompt.md`` and ``card.md``
beside it, exactly the shape ``preset_spec.load_dir`` reads) plus what a reader of the deposit
needs: ``fields.md`` (every field documented from the declaration), ``CITATION.cff`` and a
README naming the DOI, the schema id and the engine. Zenodo reserves the DOI before the files
go up, so the files name a DOI they are given — the same trick ``release_export`` plays.
"""
from __future__ import annotations

import re

import copy
import json

from . import preset_spec as ps, release_export

ENGINE_REPO = "https://github.com/johanna-einsiedler/metalens"


def _yaml(s) -> str:
    return json.dumps(str(s), ensure_ascii=False)       # a JSON string is a valid YAML double-quoted scalar


def citation_cff(spec: dict, release: dict, *, doi: str | None, concept_doi: str | None, card_url: str | None) -> str:
    meta = spec.get("meta") or {}
    cm = ps.card_meta(spec)
    what = "import schema" if meta.get("mode") == "import" else "extraction preset"
    lines = ["cff-version: 1.2.0", f"message: {_yaml('If you use this preset, please cite it as below.')}", "type: software",
             "title: " + _yaml(f"{meta.get('title') or spec['id']}: Metalens {what}"),
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
    if concept_doi:
        lines += ["identifiers:", f"  - type: doi", f"    value: {_yaml(concept_doi)}",
                  f"    description: {_yaml('The concept DOI: resolves to the newest version of this preset.')}"]
    return "\n".join(lines) + "\n"


def readme(spec: dict, release: dict, *, doi: str | None, concept_doi: str | None, card_url: str | None) -> str:
    from . import preset_releases
    meta = spec.get("meta") or {}
    cm = ps.card_meta(spec)
    eng = release.get("engine") or {}
    head = (f"Frozen on {str(release.get('created_at') or '')[:10]} · schema `{release['schema_id']}` · spec sha256 "
            f"`{release['spec_sha']}` · engine {eng.get('name') or 'metalens'} {eng.get('version') or ''}"
            + (f" ({eng.get('git_sha')[:8]})" if eng.get("git_sha") else ""))
    if doi:
        head += f" · DOI [{doi}](https://doi.org/{doi})"
    url = card_url or ""
    lines = [f"# {meta.get('title') or spec['id']} — preset release v{release['number']}", "", head, ""]
    if meta.get("description"):
        lines += ["## What this is", "", meta["description"], ""]
    if release.get("notes"):
        lines += ["## Release notes", "", release["notes"], ""]
    lines += ["## How to cite", "", "This version:", "",
              "> " + preset_releases.citation(spec, release, concept_doi=doi, url=url), ""]
    if concept_doi:
        lines += ["Any version (the concept DOI follows the newest):", "",
                  "> " + preset_releases.citation(spec, release, concept_doi=concept_doi, url=url), ""]
    lines += ["## Files", "",
              "- `preset.json` — the declaration (Metalens preset format 2) as released; put it in a folder named by "
              "`PAPERLENS_PRESET_DIRS`, or in `<data>/presets/` of a local Metalens (`uvx metalens`), or load it under "
              "My workspace → Presets → Load preset file, and Metalens runs it",
              *(["- `prompt.md` — the author's task text, read by `preset.json`"] if meta.get("mode") != "import" else
                ["- (no prompt: this is an import schema — the records are produced outside Metalens)"]),
              *([f"- `card.md` — the card: {', '.join(card_sections(cm.get('text') or '')) or 'its prose'}"] if cm.get("text") else []),
              "- `fields.md` — every field the preset extracts, with type, evidence policy and confidence group",
              "- `CITATION.cff` — citation metadata", ""]
    if cm["related"]:
        lines += ["## Related", ""]
        for r in cm["related"]:
            where = f" https://doi.org/{r['doi']}" if r.get("doi") else (f" {r['url']}" if r.get("url") else "")
            lines.append(f"- {r['kind']}: {r['title']}{where}")
        lines.append("")
    lines += [f"Licence: {cm.get('license') or 'see the card'}. Made with Metalens ({ENGINE_REPO}).", ""]
    return "\n".join(lines)


def for_deposit(card_md: str, site: str | None = None, card_url: str | None = None) -> str:
    """The card as a file of its own: a link the card page turns into a "to be published soon"
    tooltip (``[text](#to-be-published)``) has no target outside the page, so it becomes plain text;
    a figure the page draws (a ```figure fence) becomes a pointer to it on the card page; a path on
    the site (another preset's card) becomes a full address on it."""
    from . import brands
    site = (site or brands.public_url()).rstrip("/")
    out = re.sub(r"\[([^\]]+)\]\(#to-be-published\)", r"\1 (to be published)", card_md)
    def figure(m_):
        what = " ".join(m_.group(2).split()).rstrip(".") or "Figure"
        return f"*Figure: {what}. It is drawn on the card page" + (f": {card_url}" if card_url else "") + ".*"
    out = re.sub(r"(?m)^(`{3,})figure\s+[a-z0-9-]+[ \t]*\n(.*?)^\1[ \t]*$", figure, out, flags=re.S)
    return re.sub(r"\]\((/[A-Za-z0-9][^)\s]*)\)", lambda m: f"]({site}{m.group(1)})", out)


def card_sections(card_md: str) -> list[str]:
    """The card's level-2 headings, in order (outside code fences)."""
    out, fence = [], None
    for line in card_md.splitlines():
        m = re.match(r"^(`{3,}|~{3,})", line)
        if m:
            fence = None if fence and m.group(1).startswith(fence) else (fence or m.group(1))
            continue
        if not fence and line.startswith("## "):
            out.append(line[3:].strip())
    return out


def build(spec: dict, release: dict, *, doi: str | None = None, concept_doi: str | None = None,
          card_url: str | None = None) -> dict[str, bytes]:
    """{relative path: bytes} for one release — a loadable preset directory plus its papers."""
    out = copy.deepcopy(spec)
    files: dict[str, str] = {}
    prompt = out.setdefault("prompt", {})
    text = prompt.pop("text", None)
    if text and (out.get("meta") or {}).get("mode") != "import":
        files["prompt.md"] = text
        out["prompt"] = {"file": "prompt.md", **{k: v for k, v in prompt.items() if k != "file"}}
    card_text = (out.get("card") or {}).get("text")
    if card_text:
        files["card.md"] = for_deposit(card_text, card_url=card_url)
        out["card"] = {"file": "card.md"}
    files["preset.json"] = json.dumps(out, indent=2, ensure_ascii=False) + "\n"
    files["fields.md"] = ps.fields_markdown(spec)
    files["CITATION.cff"] = citation_cff(spec, release, doi=doi, concept_doi=concept_doi, card_url=card_url)
    files["README.md"] = readme(spec, release, doi=doi, concept_doi=concept_doi, card_url=card_url)
    return {k: v.encode("utf-8") for k, v in files.items()}


def as_zip(files: dict[str, bytes], preset_id: str, number: int) -> bytes:
    return release_export.as_zip(files, f"{preset_id}-v{number}")
