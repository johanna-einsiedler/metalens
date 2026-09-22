"""Audit report of a dataset: what the model extracted, what the human review changed.

For every paper the ORIGINAL model response (kept verbatim at extraction / import time) is
ingested again and compared, value by value, with the records as they stand now. That makes
the report independent of the edit log: a deleted entry, a removed table row and a manually
added value all show up, which the event history alone cannot give (deleting a record
cascades its events).

Per extraction target (a field, a table's value column, a sub-entry field) the report counts

    extracted   non-null values in the model output
    reviewed    of those, the ones inside human-reviewed entries (verified, or deleted)
    unchanged   reviewed values that stand as extracted                      → TP
    changed     reviewed values the human replaced        (a wrong value)    → FP and FN
    removed     reviewed values the human deleted         (overextraction)   → FP
    added       values the human supplied                 (an omission)      → FN

and from them sensitivity TP/(TP+FN), precision TP/(TP+FP) and Jaccard TP/(TP+FN+FP), the
indices of Schroeders, Gnambs & Einsiedler (2026) with the human-reviewed state as reference.
A wrong value is penalised twice, as there. Entries nobody has verified yet do not enter the
indices (their values are not a reference), only the ``extracted`` count.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from . import records, storage
from .ingest import ingest

_SKIP_KEYS = {"notes", "evidence", "confidence", "extraction_confidence", "_rid"}


# ── values ───────────────────────────────────────────────────────────────────
def _blank(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    if isinstance(a, str) and isinstance(b, str):
        return a.strip() == b.strip()
    return a == b


def _is_rows(v: Any) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


class _Tally:
    __slots__ = ("extracted", "reviewed", "unchanged", "changed", "removed", "added")

    def __init__(self) -> None:
        self.extracted = self.reviewed = self.unchanged = self.changed = self.removed = self.added = 0

    def value(self, orig: Any, cur: Any, reviewed: bool) -> bool:
        """Count one value; returns True when it differs (an edit)."""
        o, c = not _blank(orig), not _blank(cur)
        if o:
            self.extracted += 1
        if not o and not c:
            return False
        differs = not (o and c and _same(orig, cur))
        if not reviewed:
            return differs
        if o:
            self.reviewed += 1
        if o and c and not differs:
            self.unchanged += 1
        elif o and c:
            self.changed += 1
        elif o:
            self.removed += 1
        else:
            self.added += 1
        return differs

    def indices(self) -> dict:
        tp, fp, fn = self.unchanged, self.changed + self.removed, self.changed + self.added
        r = lambda a, b: round(a / b, 4) if b else None   # noqa: E731
        return {"sen": r(tp, tp + fn), "pre": r(tp, tp + fp), "jac": r(tp, tp + fn + fp)}

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__} | self.indices()


# ── the preset's declaration, reduced to what the comparison needs ───────────
def _layout(spec: dict | None) -> dict:
    """{targets: [(target id, label, group)], tables: {name: {key, values}}, children: {key: [field names]},
    skip: set} from a format-2 spec; an empty layout (everything inferred) without one."""
    out = {"labels": {}, "groups": {}, "order": [], "tables": {}, "children": {}, "skip": set(_SKIP_KEYS)}
    if not isinstance(spec, dict):
        return out
    ent = spec.get("entries") or {}
    display = spec.get("display") or {}
    declared = (display.get("audit") or {}) if isinstance(display.get("audit"), dict) else {}
    tab_of = {}
    for t in display.get("tabs") or []:
        for f in (t or {}).get("fields") or []:
            tab_of.setdefault(f, (t or {}).get("label") or "")

    def add(target: str, label: str, group: str) -> None:
        out["labels"][target] = label; out["groups"][target] = group; out["order"].append(target)

    for f in ent.get("fields") or []:
        name, ftype = f.get("name"), f.get("type")
        if not name:
            continue
        if ftype == "text" or name in _SKIP_KEYS or name == ent.get("id_field"):   # free text and the entry's own label are not targets
            out["skip"].add(name); continue
        group = tab_of.get(name, "")
        if ftype == "table":
            cols = [c if isinstance(c, dict) else {"name": c} for c in f.get("columns") or []]
            key = list((declared.get(name) or {}).get("key") or [c["name"] for c in cols if c.get("type") in ("string", "enum")])
            vals = [c for c in cols if c["name"] not in key and c.get("type") != "text"]
            out["tables"][name] = {"key": key, "values": [c["name"] for c in vals]}
            for c in vals:
                lab = f.get("label") or name
                add(f"{name}.{c['name']}", lab if len(vals) == 1 else f"{lab}: {c.get('label') or c['name']}", group)
        else:
            add(name, f.get("label") or name, group)
    for ch in ent.get("children") or []:
        ck = ch.get("key")
        if not ck:
            continue
        names = []
        for f in ch.get("fields") or []:
            if f.get("type") == "text" or f.get("name") in _SKIP_KEYS or not f.get("name"):
                continue
            names.append(f["name"])
            add(f"{ck}.{f['name']}", f.get("label") or f["name"], tab_of.get(ck, ch.get("label") or ""))
        out["children"][ck] = names
    # rows follow the review tabs (Factor loadings, Correlations, Descriptives …), each group once
    tab_order = {(t or {}).get("label") or "": i for i, t in enumerate(display.get("tabs") or [])}
    out["order"].sort(key=lambda t: tab_order.get(out["groups"].get(t, ""), len(tab_order)))
    return out


# ── rows: match the model's rows to the reviewed rows ────────────────────────
def _match_rows(orig: list[dict], cur: list[dict], key: list[str]) -> list[tuple[int | None, int | None]]:
    """Pairs (i, j); (i, None) = a removed row, (None, j) = an added row. With declared key
    columns a pair must agree on all of them; otherwise on at least half of its filled cells."""
    by_id = {c.get("_rid"): j for j, c in enumerate(cur) if c.get("_rid")}
    fixed = {i: by_id[o["_rid"]] for i, o in enumerate(orig) if o.get("_rid") in by_id}
    if fixed and len(fixed) == len(set(fixed.values())):          # ids present on both sides: exact pairing
        rest_o = [i for i in range(len(orig)) if i not in fixed]
        rest_c = [j for j in range(len(cur)) if j not in set(fixed.values())]
        more = _match_rows([{k: v for k, v in orig[i].items() if k != "_rid"} for i in rest_o],
                           [{k: v for k, v in cur[j].items() if k != "_rid"} for j in rest_c], key)
        return (list(fixed.items())
                + [(rest_o[i] if i is not None else None, rest_c[j] if j is not None else None) for i, j in more])
    cand = []
    for i, o in enumerate(orig):
        for j, c in enumerate(cur):
            cols = [k for k in set(o) | set(c) if k not in _SKIP_KEYS]
            equal = sum(1 for k in cols if not _blank(o.get(k)) and _same(o.get(k), c.get(k)))
            if key:
                if not all(_same(o.get(k), c.get(k)) for k in key):
                    continue
            else:
                filled = sum(1 for k in cols if not _blank(o.get(k)) or not _blank(c.get(k)))
                if equal == 0 or equal * 2 < filled:
                    continue
            cand.append((-equal, abs(i - j), i, j))
    cand.sort()
    used_i, used_j, pairs = set(), set(), []
    for _e, _d, i, j in cand:
        if i in used_i or j in used_j:
            continue
        used_i.add(i); used_j.add(j); pairs.append((i, j))
    pairs += [(i, None) for i in range(len(orig)) if i not in used_i]
    pairs += [(None, j) for j in range(len(cur)) if j not in used_j]
    return pairs


def _compare_entry(orig: dict | None, cur: dict | None, reviewed: bool, layout: dict, tallies: dict) -> int:
    """Tally one entry; returns the number of differing values (edits)."""
    o, c = orig or {}, cur or {}
    edits = 0
    tally = lambda t: tallies.setdefault(t, _Tally())   # noqa: E731
    for name in dict.fromkeys(list(o) + list(c)):
        if name in layout["skip"]:
            continue
        ov, cv = o.get(name), c.get(name)
        if name in layout["tables"] or name in layout["children"] or _is_rows(ov) or _is_rows(cv):
            decl = layout["tables"].get(name)
            key = decl["key"] if decl else []
            orows = ov if _is_rows(ov) else []
            crows = cv if _is_rows(cv) else []
            if decl:
                vcols = decl["values"]
            elif name in layout["children"]:
                vcols = layout["children"][name]
            else:                                           # undeclared: every column that occurs
                vcols = [k for k in dict.fromkeys(k for r in orows + crows for k in r) if k not in _SKIP_KEYS and k not in key]
            for i, j in _match_rows(orows, crows, key):
                for col in vcols:
                    edits += tally(f"{name}.{col}").value(orows[i].get(col) if i is not None else None,
                                                          crows[j].get(col) if j is not None else None, reviewed)
        else:
            edits += tally(name).value(ov, cv, reviewed)
    return edits


# ── the original model output of one document ────────────────────────────────
def _original_records(store, document_id: str, entries_key: str | None) -> dict[int, dict] | None:
    key = storage.raw_key(document_id)
    try:
        if not store.exists(key):
            return None
        text = store.get(key).decode("utf-8", "replace")
    except Exception:                                       # noqa: BLE001 - storage hiccup = no original
        return None
    for candidate in (text, _json_block(text)):
        if not candidate:
            continue
        try:
            res = ingest(json.loads(candidate) if candidate.lstrip().startswith("{") else candidate, entries_key=entries_key)
            return {r.entry_index: r.field_values for r in res.records}
        except Exception:                                   # noqa: BLE001 - try the next reading
            continue
    return None


def _json_block(text: str) -> str | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    return m.group(0) if m else None


# ── the report ───────────────────────────────────────────────────────────────
def dataset_audit(conn, dataset_id: str, *, store=None) -> dict | None:
    d = records.get_dataset(conn, dataset_id)
    if d is None:
        return None
    store = store or storage.get_store()
    rows = conn.execute(
        """SELECT r.document_id::text, r.entry_index, r.field_values, r.verification_status, r.schema_id
           FROM record r WHERE r.dataset_id = %s::uuid AND NOT COALESCE(r.screened_empty, false)
           ORDER BY r.document_id, r.entry_index""", (dataset_id,)).fetchall()
    docs = conn.execute(
        """SELECT DISTINCT r.document_id::text, ed.schema_id FROM record r
           JOIN extraction_document ed ON ed.id = r.document_id WHERE r.dataset_id = %s::uuid""", (dataset_id,)).fetchall()
    current: dict[str, dict[int, tuple[dict, str]]] = {}
    for doc_id, ei, fv, status, _sid in rows:
        current.setdefault(doc_id, {})[ei] = (fv or {}, status)

    specs: dict[str | None, dict | None] = {}
    tallies: dict[str, _Tally] = {}
    layout_first: dict | None = None
    n_entries = n_reviewed = n_removed_entries = n_added_entries = edits_outside = 0
    without_original: list[str] = []
    for doc_id, schema_id in docs:
        if schema_id not in specs:
            try:
                specs[schema_id] = records.schema_spec(conn, schema_id) if schema_id else None
            except Exception:                               # noqa: BLE001
                specs[schema_id] = None
        spec = specs[schema_id]
        layout = _layout(spec)
        if layout_first is None and layout["order"]:
            layout_first = layout
        entries_key = ((spec or {}).get("entries") or {}).get("key")
        orig = _original_records(store, doc_id, entries_key)
        cur = current.get(doc_id, {})
        if orig is None:
            without_original.append(doc_id)
            continue
        for ei in sorted(set(orig) | set(cur)):
            o = orig.get(ei)
            c, status = cur.get(ei, (None, None))
            reviewed = c is None or o is None or status == "verified"    # deleted / manual / verified
            n_entries += 1 if o is not None else 0
            n_removed_entries += 1 if c is None else 0
            n_added_entries += 1 if o is None else 0
            n_reviewed += 1 if (reviewed and o is not None) else 0
            edits = _compare_entry(o, c, reviewed, layout, tallies)
            if not reviewed:
                edits_outside += edits

    layout = layout_first or {"labels": {}, "groups": {}, "order": []}
    order = [t for t in layout["order"] if t in tallies] + [t for t in tallies if t not in layout["order"]]
    out_rows = []
    total = _Tally()
    for t in order:
        tl = tallies[t]
        if not (tl.extracted or tl.added):
            continue
        for k in _Tally.__slots__:
            setattr(total, k, getattr(total, k) + getattr(tl, k))
        out_rows.append({"target": t, "label": layout["labels"].get(t, t.replace("_", " ").replace(".", ": ")),
                         "group": layout["groups"].get(t, ""), **tl.as_dict()})
    return {
        "dataset_id": dataset_id, "title": d.get("title"), "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": d.get("model"), "schema_id": d.get("schema_id"),
        "n_papers": len(docs), "n_papers_without_original": len(without_original),
        "n_entries": n_entries, "n_entries_reviewed": n_reviewed,
        "n_entries_removed": n_removed_entries, "n_entries_added": n_added_entries,
        "edits_in_unreviewed_entries": edits_outside,
        "rows": out_rows, "total": total.as_dict(),
    }
