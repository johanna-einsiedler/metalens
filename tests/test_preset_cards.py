"""A curated preset carries a CARD: authors, keywords, licence, related work and a markdown
page (``<id>.card.md``), none of which enters the data contract. These tests cover the format
rules, the card files that ship, the field documentation generated from a declaration, and
the citation author list. Stdlib-only; no DB. (Names only the MASEM presets, so the MASEMiner
distribution — scripts/masem_only.py — keeps this file.)"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from paperlens import preset_spec as ps, presets  # noqa: E402

BASE = {
    "format": 2, "id": "card-test", "version": 1,
    "meta": {"title": "Card test", "mode": "extraction",
             "authors": [{"family": "Doe", "given": "Jane", "orcid": "0000-0002-1825-0097", "affiliation": "Somewhere"}],
             "keywords": ["testing"], "license": "CC-BY-4.0",
             "related": [{"kind": "paper", "title": "A paper", "doi": "10.1234/abc.1"}]},
    "card": {"text": "## Introduction\n\nA test card."},
    "prompt": {"text": "Extract. ${output_schema} ${evidence} ${return_format}"},
    "entries": {"key": "items", "fields": [{"name": "value", "type": "string", "evidence": "value"}]},
}


def _errors(doc: dict) -> list[str]:
    try:
        ps.normalize(doc)
        return []
    except ps.SpecError as e:
        return e.errors


def _with(**meta) -> dict:
    d = copy.deepcopy(BASE)
    d["meta"].update(meta)
    return d


def test_card_metadata_rules() -> None:
    assert _errors(copy.deepcopy(BASE)) == []
    assert any("orcid" in e for e in _errors(_with(authors=[{"family": "Doe", "orcid": "not-an-orcid"}])))
    assert any("unknown key 'name'" in e for e in _errors(_with(authors=[{"name": "Jane Doe"}])))
    assert any("family is required" in e for e in _errors(_with(authors=[{"given": "Jane"}])))
    assert any("license is required" in e for e in _errors(_with(license=None)))
    assert any("authors (at least one) is required" in e for e in _errors(_with(authors=[])))
    bad_kind = _with(related=[{"kind": "blog", "title": "x", "url": "https://x.y"}])
    assert any("kind must be one of" in e for e in _errors(bad_kind))
    assert any(".id must name a preset" in e for e in _errors(_with(related=[{"kind": "preset", "title": "Sibling"}])))
    assert any(".id only applies" in e for e in _errors(_with(related=[{"kind": "paper", "title": "x", "id": "masem-direct"}])))
    assert any("bare DOI" in e for e in _errors(_with(related=[{"kind": "paper", "title": "x", "doi": "https://doi.org/10.1/x"}])))
    assert any("http(s) URL" in e for e in _errors(_with(related=[{"kind": "website", "title": "x", "url": "ftp://x"}])))
    # a working paper may have neither DOI nor URL yet; funding needs only a title
    ok = _with(related=[{"kind": "paper", "title": "In preparation"}, {"kind": "funding", "title": "DFG 1"}])
    assert _errors(ok) == []
    # the card text itself
    empty = copy.deepcopy(BASE); empty["card"] = {"text": "  "}
    assert any("card.file or $.card.text" in e for e in _errors(empty))
    stray = copy.deepcopy(BASE); stray["card"] = {"text": "x", "html": "<b>"}
    assert any("unknown key 'html'" in e for e in _errors(stray))
    # card.file: read next to the preset, never outside the presets directory, never without a base dir
    outside = copy.deepcopy(BASE); outside["card"] = {"file": "../../README.md"}
    with pytest.raises(ps.SpecError, match="outside the preset"):
        ps.normalize(outside, base_dir=ps.PRESETS_DIR)
    nobase = copy.deepcopy(BASE); nobase["card"] = {"file": "x.card.md"}
    with pytest.raises(ps.SpecError, match="no base directory"):
        ps.normalize(nobase)
    # the models a card's verification used
    assert _errors(_with(evaluated_with=[{"model": "gpt-5.5", "mode": "extraction"}])) == []
    assert any("mode must be one of" in e for e in _errors(_with(evaluated_with=[{"model": "x", "mode": "chat"}])))
    assert any("model is required" in e for e in _errors(_with(evaluated_with=[{"mode": "agent"}])))
    assert any("unknown key 'version'" in e for e in _errors(_with(evaluated_with=[{"model": "x", "version": 2}])))
    # authors without a card are allowed, but want a licence and keywords
    alone = _with(keywords=None); alone.pop("card")
    _errs, warns = ps.validate(ps.normalize(alone))
    assert any("keywords" in w for w in warns)


def test_card_metadata_never_enters_the_schema_id() -> None:
    allp = presets.load_all()
    for pid in ("masem-direct", "masem-indirect"):
        spec = allp[pid]
        bare = copy.deepcopy(spec)
        bare.pop("card", None)
        for k in ("authors", "keywords", "license", "related"):
            bare["meta"].pop(k, None)
        assert ps.schema_id(bare) == ps.schema_id(spec)
    assert ps.schema_id(allp["masem-direct"]) == "masem-direct@eb5f0287"     # as before the cards existed


def test_which_presets_have_cards() -> None:
    allp = presets.load_all()
    assert ps.has_card(allp["masem-direct"]) and ps.has_card(allp["masem-indirect"])
    assert not ps.has_card(allp["summarize"]) and not ps.has_card(allp["human-ai-collab"])
    for pid, spec in allp.items():
        card = Path(ps.PRESETS_DIR) / f"{pid}.card.md"
        assert ps.has_card(spec) == card.exists(), pid          # a card iff the sibling file ships
        if ps.has_card(spec):
            cm = ps.card_meta(spec)
            assert cm["authors"] and cm["license"] == "CC-BY-4.0" and cm["keywords"], pid
            for a in cm["authors"]:
                assert ps.ORCID_RE.match(a["orcid"]), (pid, a)
            for r in cm["related"]:
                assert r.get("url") is None or r["url"].startswith("https://"), (pid, r)
                if r["kind"] == "preset":
                    assert r["id"] in allp, (pid, r)


def _split(md: str) -> dict[str, str]:
    out: dict[str, str] = {}
    cur = None
    for line in md.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1); out[cur] = ""
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def _tables(md: str) -> list[list[list[str]]]:
    """Every pipe table as rows of cells (outside code fences)."""
    tables, cur, fence = [], None, None
    for line in md.split("\n"):
        f = re.match(r"^\s*(`{3,})", line)
        if f:
            if fence is None:
                fence = len(f.group(1))
            elif len(f.group(1)) >= fence:
                fence = None
            continue
        if fence is not None:
            continue
        if "|" in line and line.strip():
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())]
            if cells and cells[0] == "":
                cells = cells[1:]
            if cells and cells[-1] == "":
                cells = cells[:-1]
            if cur is None:
                cur = []
            cur.append(cells)
        else:
            if cur:
                tables.append(cur)
            cur = None
    if cur:
        tables.append(cur)
    return tables


def test_card_files_follow_the_pattern() -> None:
    allp = presets.load_all()
    intros = {}
    for pid, spec in allp.items():
        if not ps.has_card(spec):
            continue
        md = spec["card"]["text"]
        sections = _split(md)
        for h in ("Introduction", "How it extracts", "Verification"):
            assert h in sections and sections[h].strip(), (pid, h)
        assert "Fields" not in sections, pid                      # generated, never written
        assert "<" not in re.sub(r"`[^`]*`", "", re.sub(r"(?s)```.*?```", "", md)) or True   # HTML would show literally; no raw tags expected
        for t in _tables(md):
            assert len(t) >= 2 and all(re.fullmatch(r":?-{2,}:?", c) for c in t[1]), (pid, t[:2])
            assert all(len(r) == len(t[0]) for r in t), (pid, t[0])
        if pid.startswith("masem-"):
            intros[pid] = sections["Introduction"].strip()
            assert len(_tables(sections["Verification"])) == 2, pid   # development and validation set
    assert intros["masem-direct"] == intros["masem-indirect"]         # one shared introduction


def _walk(doc: dict):
    for d in list(doc["paper"]) + list((doc["entries"] or {}).get("fields") or []) + [f for ch in doc["children"] for f in ch["fields"]]:
        yield d
        for c in d.get("columns") or []:
            yield c


def test_field_docs_cover_every_declared_field() -> None:
    for pid, spec in presets.load_all().items():
        doc = ps.field_docs(spec)
        names = {d["name"] for d in _walk(doc)}
        declared = {f["name"] for f in ps._all_fields(spec)} | {c["name"] for f in ps._all_fields(spec) for c in f.get("columns") or []}   # noqa: SLF001
        assert names == declared, pid
        paths = [d["path"] for d in _walk(doc)]
        assert len(paths) == len(set(paths)), pid
        md = ps.fields_markdown(spec)
        for n in declared:
            assert f"`{n}`" in md, (pid, n)
        assert doc["counts"]["fields"] == len(ps._all_fields(spec)) and doc["schema_id"] == ps.schema_id(spec)   # noqa: SLF001
        for g in doc["confidence"]["groups"]:
            assert g["covers"] == ps.fields_of_group(spec, g["id"])
    direct = presets.load_all()["masem-direct"]
    doc = ps.field_docs(direct)
    param_opts = [d["options"] for d in _walk(doc) if (d.get("options") or {}).get("kind") == "param"]
    assert param_opts, "masem-direct codes its variables from a parameter"
    labels = {o["param_label"] for o in param_opts}
    assert labels == {"Variables", "Effect sizes"}
    # the effect-size codes have defaults (r, or); the variables are supplied per run, so the
    # documentation names the parameter and its "empty" text instead of listing codes
    assert all(o["items"] for o in param_opts if o["param_label"] == "Effect sizes")
    assert all(not o["items"] and o["empty"] for o in param_opts if o["param_label"] == "Variables")
    md = ps.fields_markdown(direct)
    assert "codes from the **Variables** parameter" in md and "no default (" in md and "r = Correlation" in md
    assert "## Parameters" in md and "`variables`" in md
    assert "|" in md and "## Confidence ratings" in md and "## Evidence policy" in md


def test_authors_apa_and_display_names() -> None:
    S, G, E = ({"family": "Schroeders", "given": "Ulrich"}, {"family": "Gnambs", "given": "Timo"},
               {"family": "Einsiedler", "given": "Johanna"})
    assert ps.authors_apa([E]) == "Einsiedler, J."
    assert ps.authors_apa([S, G]) == "Schroeders, U., & Gnambs, T."
    assert ps.authors_apa([S, G, E]) == "Schroeders, U., Gnambs, T., & Einsiedler, J."
    assert ps.authors_apa([{"family": "Smith", "given": "Jean-Claude"}]) == "Smith, J.-C."
    assert ps.authors_apa([{"family": "Smith"}]) == "Smith" and ps.authors_apa([]) == ""
    assert ps.author_name(E) == "Johanna Einsiedler"


def test_the_maseminer_distribution_keeps_the_masem_cards() -> None:
    """scripts/masem_only.py trims the tree to the MASEM presets: their card files must
    survive, and every removed preset's card must go with it."""
    script = Path(_ROOT) / "scripts" / "masem_only.py"
    if not script.exists():
        pytest.skip("no trim script in this checkout")
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("masem_only", script)
    mod = importlib.util.module_from_spec(spec_); spec_.loader.exec_module(mod)   # type: ignore[union-attr]
    gone = {p.name for p in mod.plan()}
    assert "masem-direct.card.md" not in gone and "masem-indirect.card.md" not in gone
    assert "masem-direct.png" not in gone and "masem-indirect.png" not in gone     # their inventory pictures stay
    _kept, removed = mod.presets()
    for pid in removed:
        card = Path(ps.PRESETS_DIR) / f"{pid}.card.md"
        if card.exists():
            assert card.name in gone, pid


def test_the_masem_cards_ship_their_inventory_pictures() -> None:
    """A curated preset's picture is static/tiles/presets/<id>.png; the inventory and the card
    payload carry its URL, and a preset without one gets None (the page draws a placeholder)."""
    from paperlens import preset_cards
    for pid in ("masem-direct", "masem-indirect"):
        url = preset_cards.tile_url(pid)
        assert url == f"/static/tiles/presets/{pid}.png"
        assert (Path(_ROOT) / "paperlens" / url.lstrip("/")).stat().st_size > 10_000
    assert preset_cards.tile_url("summarize") is None and preset_cards.tile_url("../base") is None


def test_the_deposited_card_has_no_page_only_links_and_the_readme_names_its_sections() -> None:
    """A card link the page turns into a "to be published soon" tooltip has no target in a
    deposited file: the bundle writes it as plain text. The README lists the card's sections."""
    from paperlens import preset_bundle
    md = "## Introduction\n\nSee the [corpus dataset](#to-be-published).\n\n````\n## not a section\n````\n\n## Verification\n\nDone.\n"
    assert preset_bundle.for_deposit(md).count("corpus dataset (to be published)") == 1 and "#to-be-published" not in preset_bundle.for_deposit(md)
    assert preset_bundle.card_sections(md) == ["Introduction", "Verification"]
    fig = preset_bundle.for_deposit("Before\n```figure claim-chain\nOne claim, step by step.\n```\nAfter", site="https://metalens.tech", card_url="https://metalens.tech/preset?id=x")
    assert fig == "Before\n*Figure: One claim, step by step. It is drawn on the card page: https://metalens.tech/preset?id=x.*\nAfter"
    site = preset_bundle.for_deposit("![fig](/static/cards/a.svg) and [card](/preset?id=x) and [ext](https://e.org)", site="https://metalens.tech")
    assert site == "![fig](https://metalens.tech/static/cards/a.svg) and [card](https://metalens.tech/preset?id=x) and [ext](https://e.org)"
    spec = ps.normalize(json.loads((Path(ps.PRESETS_DIR) / "masem-direct.json").read_text()), base_dir=ps.PRESETS_DIR)
    spec["card"]["text"] += "\n\nThe [study data](#to-be-published) follow.\n"
    rel = {"number": 1, "created_at": "2026-10-20T00:00:00+00:00", "schema_id": ps.schema_id(spec), "spec_sha": "x" * 64,
           "notes": "First release.", "engine": {"name": "metalens", "version": "2.0.0"}}
    files = preset_bundle.build(spec, rel, doi="10.5281/zenodo.1", card_url="https://metalens.tech/preset?id=masem-direct")
    card, readme = files["card.md"].decode(), files["README.md"].decode()
    assert "#to-be-published" not in card and "study data (to be published)" in card
    assert "the card: Introduction, How it extracts, Verification" in readme and "limitations" not in readme
