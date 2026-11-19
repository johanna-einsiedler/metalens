"""The Metalens document of a paper under the extraction contract (``genai-work-extraction``,
the bundled v20) from the chain's record — a port of the review project's assisted import
(``metalens.import_file`` with ``parts``: every cell of the record with its evidence row, the
tasks' outcomes with their quotes, the result rows' quote parts each an evidence row with the
exhibit as its source) and of the review fields ``validation_imports.pilot_fields`` sets
(the units' own words, the composite outcomes, the abstract's claims matched to their units,
every exhibit with its cited cells and its transcription as an evidence item). The flags,
the routing and the triage score are the route compute's (P4): left empty here.

The ``assemble`` compute is registered here: it reads the four passes, builds the record
(``record.finish``), the headlines and the document, and hands the engine ``_record_chain`` —
what the readings agreed on per task, from the matchers' ``_runs`` annotations.
"""
from __future__ import annotations

import re

from . import checks as K
from . import headlines as H
from . import record as R
from . import taxonomy as T
from . import text as X

UNIT_COLS = ("task", "arms", "outcome", "subgroup", "horizon")


def _int(x):
    try:
        return int(x) if x not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _runs_note(node):
    """What the readings disagreed on, for the coder: 'runs: returned by 1 of 3; mean: 0.52 (2) | 0.54 (1)'."""
    rn = (node or {}).get("_runs") or {}
    parts = []
    if rn.get("of") and rn.get("found", 0) < rn["of"]:
        parts.append(f"returned by {rn['found']} of {rn['of']} runs")
    for f, alts in (rn.get("alt") or {}).items():
        parts.append(f"{f}: " + " | ".join(f"{_json(v)} ({k})" for v, k in alts))
    return "runs: " + "; ".join(parts) if parts else ""


def _json(v):
    import json
    return json.dumps(v, ensure_ascii=False)


def _conf(node):
    out = {}
    note = _runs_note(node)
    for g, c in ((node or {}).get("confidence") or {}).items():
        notes = (c or {}).get("notes") or ""
        out[g] = {"level": (c or {}).get("level") or "medium", "notes": f"{notes}  ·  {note}".strip(" ·") if note else notes}
    return out


def _with_runs(row, node, field):
    """The unit's notes field prefixed with the run note, so the disagreement is on the card itself."""
    note = _runs_note(node)
    if note:
        row[field] = f"[{note}] {row.get(field) or ''}".strip()


def _other(cell, v):
    """A coder sees the proposed term in place of `other`: an enum shows 'other: term'; a multi
    list carries the term itself as one of its values."""
    val = K.value_of(cell)
    term = (cell.get("proposed") or "").strip() if isinstance(cell, dict) else ""
    if v["open"] and term and v["type"] == "enum" and val == "other":
        return "other: " + term
    if v["open"] and term and v["type"] == "multi" and isinstance(val, list) and "other" in val:
        return [term if x == "other" else x for x in val]
    return val


def exhibit_key(s):
    """'Fig. 1, panel A' / 'Figure 1' -> 'fig 1'; 'Table A1.' -> 'table a1'."""
    s = re.sub(r"\s+", " ", str(s or "").split(",")[0].strip().lower()).rstrip(".")
    s = re.sub(r"^(figure|fig\.?)\s*", "fig ", s)
    s = re.sub(r"^(table|tab\.?)\s*", "table ", s)
    m = re.match(r"^(fig|table) ([a-z]?\d+[a-z]?)", s)
    return f"{m.group(1)} {m.group(2)}" if m else s


def exhibit_item(x, which):
    """A transcribed table or figure as an evidence item of kind "table": its caption and panels
    as markdown, the caption as what locates it in the PDF, the count of cells found as the note."""
    if which == "tables":
        md = "\n\n".join(((f"**Panel {p['panel']}**\n\n" if p.get("panel") else "") + (p.get("markdown") or "")) for p in x.get("panels") or [])
        text = f"**{x.get('id')}. {x.get('caption')}**\n\n{md}" + (f"\n\n*{x['notes']}*" if x.get("notes") else "")
        note = f"{x.get('id')}: {x.get('cells_found')}/{x.get('cells_checked')} cells found on the page"
    else:
        nums = "\n".join(f"- {n.get('label')}: {n.get('value')}" for n in x.get("numbers") or [])
        text = f"**{x.get('id')}. {x.get('caption')}**\n\n{x.get('note') or ''}\n\n{nums}"
        note = f"{x.get('id')}: {x.get('numbers_found')}/{x.get('numbers_checked')} numbers found on the page"
    return {"kind": "table", "page": int(x.get("page") or 0), "text": text, "note": note, "locate": x.get("caption") or x.get("id"),
            "owner": ("exhibit", exhibit_key(x.get("id"))), "short": x.get("id")}


# ── the units in words ──────────────────────────────────────────────────────────────────────

def arm_label(es, aid):
    for a in es.get("arms") or []:
        if a.get("arm_id") == aid:
            return a.get("label") or aid
    return aid


def measure_of(es, oid):
    for o in es.get("outcomes") or []:
        if o.get("outcome_id") == oid:
            return o.get("measure") or oid
    return oid


def unit_text(es, x):
    """An estimand in words: task · arm vs reference · outcome · subgroup · horizon."""
    return (f"{x.get('task_id')} · {arm_label(es, x.get('arm_id'))} vs {arm_label(es, x.get('vs_arm_id'))} · {measure_of(es, x.get('outcome_id'))}"
            f" · {x.get('subgroup') or 'full sample'} · {x.get('horizon') or 'post-treatment'}")


def match_unit(ids, by_head, by_key, by_est):
    """The headline unit a sentence's estimate ids point to: the unit whose headline estimate is
    among them, else a unit holding any of them through the record's rows, else None."""
    u = next((by_head[i] for i in ids if i in by_head), None)
    if u is None:
        for i in ids:
            r = by_est.get(i)
            if r:
                key = (K.value_of(r.get("finding_type")) or K.value_of(r.get("outcome_class")) or "other", r.get("_task"), K.value_of(r.get("arm")), K.value_of(r.get("vs_arm")))
                if key in by_key:
                    return by_key[key]
    return u


def matched_unit(es, by_est, task_name, u):
    """The matched unit, one cell each: task, arm vs reference, outcome, sample, horizon."""
    r = by_est.get(u.get("headline")) or {}
    return {"task": task_name.get(u.get("task"), u.get("task")), "arms": f"{arm_label(es, u.get('arm'))} vs {arm_label(es, u.get('vs_arm'))}",
            "outcome": u.get("measure") or u.get("kind"), "subgroup": K.value_of(r.get("subgroup")) or "full sample",
            "horizon": K.value_of(r.get("horizon")) or u.get("horizon") or "post-treatment"}


def contrast_rows(rec):
    """{estimate_id: row} over the record's contrast rows (the reference rows share the id; the contrast row wins)."""
    out = {}
    for t in rec.get("tasks") or []:
        for r in t.get("results") or []:
            eid = r.get("estimate_id") or r.get("estimate")
            if eid and (K.value_of(r.get("vs_arm")) or eid not in out):
                out[eid] = dict(r, _task=t.get("task_id"))
    return out


def row_quotes(r):
    parts = r.get("_quote_parts") or [[r.get("effect_quote") or r.get("stats_quote"), r.get("effect_page") or r.get("stats_page")]]
    return [(q, pg) for q, pg in parts if q]


def cited_exhibits(ex, em):
    """{exhibit id: (kind, exhibit, [(estimate, resolved)])} over the estimates whose source resolves to a table or figure."""
    groups = {}
    for e in em.get("estimates") or []:
        r = K.resolve_source(ex, e.get("source") or {}, K.first_number(e.get("effect_value"), e.get("cell_as_printed"), e.get("effect_reported")))
        if r.get("kind") in ("tables", "figures") and r.get("table"):
            groups.setdefault(r["table"]["id"], (r["kind"], r["table"], []))[2].append((e, r))
    return groups


def cell_of(kind, x, est, r):
    """(panel, row label, column, value as printed) of the cell an estimate cites in exhibit `x`."""
    src = est.get("source") or {}
    panels = x.get("panels") or []
    if kind == "tables" and r.get("row_i") is not None:
        p_ = panels[r["panel_i"]]
        row = p_["rows"][r["row_i"]]
        panel, row_label = p_.get("panel"), row[0] if row else src.get("row")
        column = (p_.get("header") or [None] * 99)[r["col_i"]] if r.get("col_i") is not None and r["col_i"] < len(p_.get("header") or []) else src.get("column")
        value = est.get("cell_as_printed") or est.get("effect_reported") or (str(r["cell"]) if r.get("cell") is not None else None)
    else:
        panel, row_label, column = (src.get("panel") if kind == "tables" else "—"), src.get("row"), src.get("column") or "—"
        value = est.get("cell_as_printed") or est.get("effect_reported")
        if kind == "figures" and not row_label:
            hit = next((n for n in x.get("numbers") or [] if est.get("effect_reported") and str(n.get("value")) == str(est.get("effect_reported"))), None)
            row_label = hit.get("label") if hit else None
    return panel, row_label, column, value


def cited_order(ests):
    return sorted(ests, key=lambda er: (er[1].get("panel_i") or 0, er[1].get("row_i") if er[1].get("row_i") is not None else 9999, str(er[0].get("estimate_id"))))


# ── the document ────────────────────────────────────────────────────────────────────────────

def build(paper: dict, fin: dict, heads: dict | None, claims: dict | None, pages: dict) -> dict:
    """The document under the extraction contract: ``paper`` the bibliographic fields the engine
    knows (title, doi, year, authors, journal, peer_reviewed), ``fin`` the record with its quotes
    (``record.finish``), ``heads`` the headlines, ``claims`` the claims pass output (none yet in
    this step), ``pages`` the normalised page texts. Returns ``{paper_metadata, tasks, evidence,
    _record_chain}``."""
    rec = fin["record"]
    es, ex, em = fin["passes"]["estimands"], fin["passes"]["exhibits"], fin["passes"]["estimates"]
    study = rec.get("study") or {}
    evidence: list[dict] = []

    def ev(snippet, page, path, source="quote"):
        if snippet and str(snippet).strip():
            evidence.append({"snippet": " ".join(str(snippet).split()), "page": X.actual_page(snippet, int(page or 0), pages), "source": source, "field": path})

    def ev_cell(cell_, path):
        if isinstance(cell_, dict) and cell_.get("quote"):
            ev(cell_["quote"], cell_.get("page"), path)

    meta = {"title": paper.get("title"), "doi": paper.get("doi"), "year": paper.get("year"), "authors": list(paper.get("authors") or []), "journal": paper.get("journal")}
    for v in T.by_level("study"):
        if v["filled"] == "model":
            cell_ = study.get(v["field"])
            meta[v["field"]] = _other(cell_, v)
            if v["ev"] == "value":
                ev_cell(cell_, f"paper_metadata.{v['field']}")
        elif v["filled"] == "code":
            meta[v["field"]] = [] if v["field"] == "evidence_items" else paper.get("peer_reviewed") if v["field"] == "peer_reviewed" else None
        elif v["filled"] == "summary":
            meta[v["field"]] = [] if v["type"] == "table" else None
        elif v["filled"] == "human":
            meta[v["field"]] = [] if v["type"] == "table" else None
    meta["confidence"] = _conf(study)
    _with_runs(meta, study, "study_notes")
    tasks = []
    record_chain: dict[str, dict] = {}
    for i, t in enumerate(rec.get("tasks") or []):
        at = f"tasks[{i}]"
        out: dict = {}
        for v in T.by_level("task"):
            if v.get("hidden"):
                continue
            if v["filled"] in ("human", "code"):
                out[v["field"]] = [] if v["type"] == "table" else None
                continue
            cell_ = t.get(v["field"])
            out[v["field"]] = _other(cell_, v)
            if v["ev"] == "value":
                ev_cell(cell_, f"{at}.{v['field']}")
        d = t.get("knowledge_education")
        if isinstance(d, dict) and d.get("quote"):
            ev(d["quote"], d.get("page"), at)
        out["confidence"] = _conf(t)
        _with_runs(out, t, "task_notes")
        arms = []
        for j, a in enumerate(t.get("arms") or []):
            aat = f"{at}.arms[{j}]"
            row: dict = {}
            for v in T.by_level("arm"):
                cell_ = a.get(v["field"])
                row[v["field"]] = _other(cell_, v)
                if v["ev"] == "value":
                    ev_cell(cell_, f"{aat}.{v['field']}")
            d = a.get("arm_description")
            if isinstance(d, dict) and d.get("quote"):
                ev(d["quote"], d.get("page"), aat)
            row["confidence"] = _conf(a)
            _with_runs(row, a, "arm_notes")
            arms.append(row)
        outcomes = []
        oq = {o.get("outcome_id"): o for o in es.get("outcomes") or []}
        for j, o in enumerate(t.get("outcomes") or []):
            outcomes.append({v["field"]: o.get(v["field"]) for v in T.by_level("outcome")})
            q = oq.get(o.get("outcome_id")) or {}
            if q.get("check") in X.FOUND and q.get("quote"):
                ev(q["quote"], q.get("page"), f"{at}.outcomes[{j}]", "outcome")
        results = []
        for j, r in enumerate(t.get("results") or []):
            rat = f"{at}.results[{j}]"
            row = {}
            for v in T.by_level("result"):
                row[v["field"]] = _other(r.get(v["field"]), v)
            row_cited = False
            qparts = [(q, pg) for q, pg in (r.get("_quote_parts") or []) if q]
            for grp, (qk, pk) in T.quote_groups().items():
                if not r.get(qk):
                    continue
                backed = [v["field"] for v in T.by_level("result") if v["qg"] == grp and r.get(v["field"]) is not None]
                for fld in backed + ([] if row_cited else [None]):
                    path = f"{rat}.{fld}" if fld else rat
                    if qparts:
                        for q, pg in qparts:
                            ev(q, pg, path, r.get("source") or "estimate")
                    else:
                        ev(r.get(qk), r.get(pk), path)
                row_cited = True
            row["confidence"] = _conf(r)
            _with_runs(row, r, "result_notes")
            results.append(row)
        out["arms"], out["results"], out["outcomes"] = arms, results, outcomes
        tasks.append(out)
        record_chain[str(i)] = _chain_entry(t)
    obj = {"paper_metadata": meta, "tasks": tasks, "evidence": evidence}
    review_fields(obj, es, ex, em, rec, heads or {"units": [], "headlines": []}, claims, pages)
    obj["_record_chain"] = record_chain
    return obj


def _chain_entry(t: dict) -> dict:
    """What the readings agreed on for one task, in the shape the record card paints: how many
    readings found the task, the fields they disagreed on (the task's own, its arms' and its
    rows') with every value read."""
    rn = t.get("_runs") or {}
    k = int(rn.get("of") or 1)
    cands: dict = {}

    def take(node, prefix):
        for f, alts in ((node.get("_runs") or {}).get("alt") or {}).items():
            adopted = K.value_of(node.get(f))
            cands[f"{prefix}{f}"] = [{"value": adopted, "runs": []}] + [{"value": v, "runs": [], "n": n} for v, n in alts]
    take(t, "")
    for j, a in enumerate(t.get("arms") or []):
        take(a, f"arms[{j}].")
    for j, r in enumerate(t.get("results") or []):
        take(r, f"results[{j}].")
    flags = [f"contested:{f}" for f in cands]
    if rn and rn.get("found", k) < k:
        flags.append("not_in_all_runs")
    return {"kind": "match", "support": {"n": int(rn.get("found") or k), "k": k}, "flags": flags, "candidates": cands, "runs": [], "trace": None}


def review_fields(obj: dict, es: dict, ex: dict, em: dict, rec: dict, heads: dict, claims: dict | None, pages: dict) -> dict:
    """The review fields of the extraction contract v20: the units' own words
    (task_description, treatment), the composite outcomes, the abstract's claims matched to
    their units (when the claims pass ran), every exhibit with its cited cells and its
    transcription as an evidence item. The flags and the routing are the route compute's."""
    meta, evidence = obj["paper_metadata"], obj["evidence"]

    def ev(snippet, page, path, source):
        if snippet:
            evidence.append({"snippet": " ".join(str(snippet).split()), "page": X.actual_page(snippet, int(page or 0), pages), "source": source, "field": path})
    desc = {t.get("task_id"): t.get("description") for t in es.get("tasks") or []}
    arms2 = {a.get("arm_id"): a for a in es.get("arms") or []}
    outs2 = {o.get("outcome_id"): o for o in es.get("outcomes") or []}
    groups, _ = H.composite_groups(es)
    measure_name = {o.get("outcome_id"): (o.get("measure") or o.get("outcome_id")) for o in es.get("outcomes") or []}
    for i, t in enumerate(obj.get("tasks") or []):
        t["task_description"] = desc.get(t.get("task_id"))
        for j, a in enumerate(t.get("arms") or []):
            a2 = arms2.get(a.get("arm_id")) or {}
            a["treatment"] = a2.get("treatment")
            if a["treatment"] and a2.get("check") in X.FOUND:
                ev(a2.get("quote"), a2.get("page"), f"tasks[{i}].arms[{j}].treatment", "arm")
        for j, o in enumerate(t.get("outcomes") or []):
            comps = [measure_name.get(c, c) for c in groups.get(o.get("outcome_id")) or []]
            o["composite_of"] = comps or None
            o2 = outs2.get(o.get("outcome_id")) or {}
            if comps and o2.get("check") in X.FOUND:
                ev(o2.get("quote"), o2.get("page"), f"tasks[{i}].outcomes[{j}].composite_of", "outcome")
    by_est = contrast_rows(rec)
    task_name = {t_.get("task_id"): (t_.get("name") or t_.get("task_id")) for t_ in es.get("tasks") or []}
    units = heads.get("units") or []
    by_head = {u.get("headline"): u for u in units if u.get("headline")}
    by_key = {(u.get("kind"), u.get("task"), u.get("arm"), u.get("vs_arm")): u for u in units}
    rows = []
    for s_ in (claims or {}).get("sentences") or []:
        ids = [str(i) for i in s_.get("estimate_ids") or []]
        if s_.get("kind") == "other" or not ids:
            continue
        u = match_unit(ids, by_head, by_key, by_est)
        cells_ = matched_unit(es, by_est, task_name, u) if u else {c: None for c in UNIT_COLS}
        r = by_est.get(u.get("headline")) if u else None
        k = len(rows)
        rows.append({"claim_id": str(s_.get("id") or f"A{k + 1}"), "sentence": s_.get("text"), "page": _int(s_.get("page")), "kind": "abstract",
                     "estimate_ids": ids, "estimand_id": K.value_of(r.get("estimand_id")) if r else None, **cells_})
        ev(s_.get("text"), s_.get("page"), f"paper_metadata.claims[{k}]", "sentence")
        for q, pg in (row_quotes(r) if r else []):
            ev(q, pg, f"paper_metadata.claims[{k}]", "estimate")
    meta["claims"] = rows
    cited = cited_exhibits(ex, em)
    estimands = {z.get("estimand_id"): z for z in es.get("estimands") or []}
    items = list(meta.get("evidence_items") or [])
    n_item = max((int(it.get("n") or 0) for it in items), default=0)
    ex_rows, cell_rows = [], []
    for kind in ("tables", "figures"):
        for x in ex.get(kind) or []:
            xid = x.get("id")
            hit = cited.get(xid)
            panels = x.get("panels") or []
            n_cells = sum(max(len(r_) - 1, 0) for p_ in panels for r_ in p_.get("rows") or []) if kind == "tables" else len(x.get("numbers") or [])
            k = len(ex_rows)
            ex_rows.append({"exhibit_id": xid, "kind": "table" if kind == "tables" else "figure", "page": _int(x.get("page")), "caption": x.get("caption"),
                            "panels": [p_.get("panel") for p_ in panels if p_.get("panel")], "n_cells": n_cells, "n_cited": len(hit[2]) if hit else 0})
            it = exhibit_item(x, kind)
            ev(it["locate"], it["page"], f"paper_metadata.exhibits[{k}]", "exhibit")
            if not hit:
                continue
            n_item += 1
            items.append({"n": n_item, "page": it["page"], "kind": "table", "text": it["text"], "note": it["note"], "bears_on": ["exhibits", "exhibit_cells"]})
            ev(it["locate"], it["page"], "paper_metadata.evidence_items", f"item {n_item} (table)")
            for est, r in cited_order(hit[2]):
                panel, row_label, column, value = cell_of(kind, x, est, r)
                z = estimands.get(est.get("estimand_id"))
                cell_rows.append({"exhibit_id": xid, "panel": panel, "row_label": row_label, "column": column, "value_as_printed": value,
                                  "se_as_printed": est.get("se_cell_as_printed"), "is_se": False, "arm_stats": False,
                                  "estimate_id": est.get("estimate_id"), "estimand_id": est.get("estimand_id"),
                                  "estimand_text": unit_text(es, z) if z else None, "role": "contrast"})
    meta["exhibits"], meta["exhibit_cells"], meta["evidence_items"] = ex_rows, cell_rows, items
    meta.setdefault("flags", [])
    meta.setdefault("routed", None)
    meta.setdefault("route_reasons", None)
    meta.setdefault("triage_score", None)
    return obj


# ── the compute ─────────────────────────────────────────────────────────────────────────────

def assemble(ctx) -> dict:
    """One document per paper from the passes: the record (derived cells, canonical ids, the
    assembly, the quotes verified, every check), the headlines from the headline plan, the
    document with its review fields (the abstract's claims from the claims pass)."""
    ex, es, em, de = (ctx.read(n) for n in ("exhibits", "estimands", "estimates", "design"))
    P = X.Pages(ctx.pages)
    fin = R.finish(ex, es, em, de, P)
    plan = ctx.reads.get("headline_plan")
    heads = H.headlines(fin["record"], plan)
    paper = {k: ctx.paper.get(k) for k in ("title", "doi", "year", "authors", "journal", "peer_reviewed")}
    obj = build(paper, fin, heads, ctx.reads.get("claims"), P.pages)
    ctx.log(f"record: {len(fin['record']['tasks'])} tasks, {sum(len(t['results']) for t in fin['record']['tasks'])} rows; quotes {len(fin['quotes']) - fin['quotes_failed']}/{len(fin['quotes'])} found; "
            f"issues {fin['issues_fail']} fail / {fin['issues_warn']} warn; {len(heads['headlines'])} headline(s)")
    return obj


def registrations() -> dict[str, dict]:
    return {
        "assemble": {"kind": "compute", "label": "Assemble the record and its Metalens document", "produces": "document",
                     "reads": ["exhibits", "estimands", "estimates", "design", "claims", "headline_plan"], "fn": assemble},
    }
