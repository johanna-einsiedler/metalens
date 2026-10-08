"""Call chains: one paper, several model calls, one reviewed document.

A preset may declare (``chain`` in preset_spec) that its extraction runs k times and/or is
followed by text-only calls that read the earlier outputs instead of the paper — an
adjudication, say. The runs are ordinary extractions persisted as hidden ``role="run"``
documents of one group; a follow-up cites EVIDENCE BY REFERENCE (``r2.claims[3].sign``)
and this module resolves every reference, transitively, to the runs' real quotes. The
final document therefore carries ordinary ``{snippet, page, source, field}`` items and
goes through the unchanged persist path (rects, ingest, issues, raw file, review UI).

What the engine knows beyond the document — how many runs hold an entry (``support``),
which values the runs disagreed on (``candidates``), ``flags`` and the hop ``trace`` —
is stored per record in ``record.chain``, never inside the publishable fields.

Three aggregation kinds: ``vote`` (deterministic, by identity fields and per-field rules),
follow-up calls (the last call's output is the document), and ``external`` (the runs and
the merged document were produced outside and imported together).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from . import preset_spec, records, storage
from .contract import ROW_ID_KEY, parse_result_json

REF_RE = re.compile(r"^(?P<src>r\d+|[a-z][a-z0-9_]*)\.(?P<path>[A-Za-z_]\w*\[\d+\]"
                    r"(?:\.[A-Za-z_]\w*\[\d+\])?(?:\.[A-Za-z_]\w*)?)$")
_PATH_RE = re.compile(r"^(?P<key>[A-Za-z_]\w*)\[(?P<i>\d+)\](?:\.(?P<child>[A-Za-z_]\w*)\[(?P<j>\d+)\])?(?:\.(?P<field>[A-Za-z_]\w*))?$")
_NONWORD = re.compile(r"[^a-z0-9]+")
PREVIOUS_BUDGET = 60_000          # characters of earlier outputs a follow-up prompt may carry
RETRY_DELAY = 15.0                # seconds before the one in-process retry of a failed call
_INTERNAL = {ROW_ID_KEY, "evidence", "confidence", "extraction_confidence"}


# ── small helpers ─────────────────────────────────────────────────────────────

def norm_text(v: Any) -> str:
    return _NONWORD.sub(" ", str(v).lower()).strip()


def canon(v: Any) -> str:
    """One string per value, so "Parental death" and "parental  death" vote together while
    0.5 and 0.50 do too (JSON canonical form for structured values)."""
    if isinstance(v, (dict, list)):
        return json.dumps(_strip(v), sort_keys=True, ensure_ascii=False)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v))
    n = norm_text(v)
    return n if n else str(v).strip().lower()      # "+" / "−" are all punctuation: keep them apart


def _strip(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _strip(v) for k, v in node.items() if k not in _INTERNAL}
    if isinstance(node, list):
        return [_strip(x) for x in node]
    return node


def _rows(node: Any) -> list:
    if isinstance(node, dict) and isinstance(node.get("_table"), list):
        return node["_table"]
    return node if isinstance(node, list) else []


def entries_of(obj: dict, key: str) -> list[dict]:
    return [e for e in _rows((obj or {}).get(key)) if isinstance(e, dict)]


def identity_key(entry: dict, fields: list[str]) -> tuple | None:
    """The tuple that makes two entries the same across runs; None when any identity value
    is missing (such an entry never matches anything)."""
    if not fields:
        return None
    out = []
    for f in fields:
        v = entry.get(f) if isinstance(entry, dict) else None
        if v is None or v == "" or v == []:
            return None
        out.append(canon(v))
    return tuple(out)


def default_rule(ftype: str | None) -> str:
    return "first" if ftype == "text" else "union" if ftype in ("list", "multi", "table") else "majority"


def _norm_path(p: str | None) -> str:
    return (p or "").strip().replace("._table[", "[")


def _sum_usage(parts: list[dict | None]) -> dict | None:
    tot: dict[str, int] = {}
    for u in parts:
        if not isinstance(u, dict):
            continue
        for k_, v in u.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                tot[k_] = tot.get(k_, 0) + int(v)
    return tot or None


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class Source:
    """One earlier output a later call may cite: a run (PDF-backed quotes in ``spans``) or
    an earlier follow-up (its own resolved ``items``)."""
    label: str
    obj: dict
    spec: dict
    spans: list[dict] = field(default_factory=list)      # {entry_index, field_path, snippet, page, source}
    items: list[dict] = field(default_factory=list)      # resolved {snippet, page, source, field, runs}
    document_id: str | None = None
    pass_index: int | None = None
    is_run: bool = True

    @property
    def key(self) -> str:
        return (self.spec.get("entries") or {}).get("key", "records")

    def quotes(self) -> list[dict]:
        """Every PDF-backed quote with the path it supports and the runs behind it."""
        if self.is_run:
            return [{"snippet": s.get("snippet"), "page": s.get("page"), "source": s.get("source"),
                     "field": _norm_path(s.get("field_path")), "entry_index": s.get("entry_index"),
                     "runs": {self.label}} for s in self.spans if s.get("snippet")]
        return self.items


# ── vote: k runs → one document, deterministically ────────────────────────────

def _apply_rule(rule: str, vals: list[tuple[int, Any]], ftype: str | None) -> tuple[Any, bool]:
    """(value, tie) for one field over the runs' values ``[(run index, value)]``."""
    if not vals:
        return None, False
    if rule == "first":
        return vals[0][1], False
    if rule in ("union", "all"):
        out: list = []
        seen: set[str] = set()
        for _, v in vals:
            for x in (v if isinstance(v, list) else [v]):
                if rule == "all" or canon(x) not in seen:
                    seen.add(canon(x)); out.append(_strip(x))
        return out, False
    counts = Counter(canon(v) for _, v in vals)
    best = max(counts.values())
    winners = [c for c, n in counts.items() if n == best]
    tie = len(winners) > 1
    for _, v in vals:                       # lowest run index among the winners
        if canon(v) in winners:
            return v, tie
    return vals[0][1], tie


def _candidates(vals: list[tuple[str, Any]]) -> list[dict]:
    """``[{value, runs}]`` for the distinct values of one field across runs (labelled)."""
    groups: dict[str, dict] = {}
    for label, v in vals:
        g = groups.setdefault(canon(v), {"value": _strip(v), "runs": []})
        if label not in g["runs"]:
            g["runs"].append(label)
    return list(groups.values())


def _entry_facts(final_entry: dict, run_entries: list[tuple[str, dict]], fields: list[dict]) -> tuple[dict, list[str]]:
    """Candidates and the flags they imply, for a final entry against the run entries
    behind it: contested fields, and a final value no run produced (rewritten)."""
    candidates: dict[str, list[dict]] = {}
    flags: list[str] = []
    for f in fields:
        name = f["name"]
        if f.get("type") in ("list", "multi", "table"):
            continue                                   # merged by union; disagreement is not a vote
        vals = [(label, e.get(name)) for label, e in run_entries if e.get(name) not in (None, "", [])]
        cands = _candidates(vals)
        if len(cands) > 1:
            candidates[name] = cands
            flags.append(f"contested:{name}")
        fv = final_entry.get(name)
        if cands and fv not in (None, "", []) and canon(fv) not in {canon(c["value"]) for c in cands}:
            flags.append(f"rewritten:{name}")
    return candidates, flags


def _source_label(orig: str | None, runs: set[str] | list[str], k: int) -> str:
    rs = sorted(set(runs), key=lambda x: (len(x), x))
    n = len(rs)
    tag = "all" if n >= k and k > 0 else "majority" if 2 * n > k else "single" if n == 1 else "some"
    return f"{orig or 'quote'} · {tag} ({', '.join(rs)})"


def _dedupe_items(items: list[dict], k: int) -> list[dict]:
    """Merge quotes that say the same thing for the same path; label each with its runs."""
    by: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for it in items:
        key_ = (norm_text(it.get("snippet")), it.get("field") or "")
        cur = by.get(key_)
        if cur is None:
            cur = by[key_] = {"snippet": it.get("snippet"), "page": it.get("page"), "orig": it.get("orig"),
                              "field": it.get("field"), "runs": set()}
            order.append(key_)
        cur["runs"] |= set(it.get("runs") or [])
        if cur.get("page") is None:
            cur["page"] = it.get("page")
    out = []
    for key_ in order:
        it = by[key_]
        out.append({"snippet": it["snippet"], "page": it["page"],
                    "source": _source_label(it.get("orig"), it["runs"], k), "field": it["field"]})
    return out


def vote(runs: list[Source], spec: dict, plan: dict) -> tuple[dict, dict[int, dict]]:
    """Merge k runs into one document by identity fields and per-field rules. Returns the
    canonical document (with the union of the runs' quotes, labelled by run) and, per final
    entry index, what the engine knows about it."""
    ent = spec.get("entries") or {}
    key = ent.get("key", "records")
    fields = ent.get("fields") or []
    children = ent.get("children") or []
    identity = plan.get("identity") or []
    rules = (plan.get("aggregate") or {}).get("fields") or {}
    k = len(runs)

    groups: list[dict] = []
    by_key: dict[tuple, dict] = {}
    for r_i, src in enumerate(runs):
        for e_i, entry in enumerate(entries_of(src.obj, key)):
            kk = identity_key(entry, identity)
            g = by_key.get(kk) if kk is not None else None
            if g is None:
                g = {"members": []}
                groups.append(g)
                if kk is not None:
                    by_key[kk] = g
            g["members"].append((r_i, e_i, entry))

    final_entries: list[dict] = []
    record_chain: dict[int, dict] = {}
    items: list[dict] = []
    for g_i, g in enumerate(groups):
        flags: list[str] = []
        per_run: dict[int, tuple[int, dict]] = {}
        for r_i, e_i, entry in g["members"]:
            if r_i in per_run:
                flags.append(f"duplicate_in_run:{runs[r_i].label}")
                continue
            per_run[r_i] = (e_i, entry)
        run_idx = sorted(per_run)
        n = len(run_idx)
        out: dict[str, Any] = {}
        for f in fields:
            name = f["name"]
            rule = rules.get(name) or default_rule(f.get("type"))
            vals = [(r_i, per_run[r_i][1].get(name)) for r_i in run_idx
                    if per_run[r_i][1].get(name) not in (None, "", [])]
            value, tie = _apply_rule(rule, vals, f.get("type"))
            if tie:
                flags.append(f"tie:{name}")
            if value is not None or any(name in per_run[r_i][1] for r_i in run_idx):
                out[name] = _strip(value) if isinstance(value, (dict, list)) else value
        # sub-entries: union of the runs' rows (matched on their scalar fields); remember
        # where each run's row landed so its quotes can follow it
        rowmap: dict[tuple[str, int, int], int] = {}
        for ch in children:
            ck = ch["key"]
            rule = rules.get(ck) or "union"
            merged: list[dict] = []
            seen: dict[str, int] = {}
            for r_i in run_idx:
                for j, row in enumerate(_rows(per_run[r_i][1].get(ck))):
                    if not isinstance(row, dict):
                        continue
                    c = canon({k_: v for k_, v in row.items() if k_ not in _INTERNAL and not isinstance(v, (list, dict))})
                    if rule == "union" and c in seen:
                        rowmap[(ck, r_i, j)] = seen[c]
                        continue
                    seen[c] = len(merged)
                    rowmap[(ck, r_i, j)] = len(merged)
                    merged.append(_strip(row))
            if merged or any(ck in per_run[r_i][1] for r_i in run_idx):
                out[ck] = merged
        for r_i in run_idx:                                     # the first run's rating stands
            if isinstance(per_run[r_i][1].get("confidence"), dict):
                out["confidence"] = per_run[r_i][1]["confidence"]
                break
        cands, cflags = _entry_facts(out, [(runs[r_i].label, per_run[r_i][1]) for r_i in run_idx], fields)
        flags += [x for x in cflags if not x.startswith("rewritten:")]
        if 2 * n <= k:
            flags.append("minority")
        # quotes follow the entry: the union of its members' items, re-pathed to the final entry
        for r_i in run_idx:
            e_i = per_run[r_i][0]
            prefix = f"{key}[{e_i}]"
            for q in runs[r_i].quotes():
                fp = q["field"]
                if not (fp == prefix or fp.startswith(prefix + ".") or (not fp.startswith(key + "[") and q.get("entry_index") == e_i)):
                    continue
                rest = fp[len(prefix):] if fp.startswith(prefix) else ""
                m = re.match(r"^\.([A-Za-z_]\w*)\[(\d+)\](.*)$", rest)
                if m and (m.group(1), r_i, int(m.group(2))) in rowmap:
                    rest = f".{m.group(1)}[{rowmap[(m.group(1), r_i, int(m.group(2)))]}]{m.group(3)}"
                elif m:
                    rest = f".{m.group(1)}"
                items.append({"snippet": q["snippet"], "page": q["page"], "orig": q.get("source"),
                              "field": f"{key}[{g_i}]{rest}", "runs": set(q.get("runs") or {runs[r_i].label})})
        final_entries.append(out)
        record_chain[g_i] = {
            "kind": "vote", "support": {"n": n, "k": k}, "flags": flags, "candidates": cands,
            "runs": [{"label": runs[r_i].label, "pass_index": runs[r_i].pass_index,
                      "document_id": runs[r_i].document_id, "entry_index": per_run[r_i][0]} for r_i in run_idx],
            "trace": None}
    pm = next((src.obj.get("paper_metadata") for src in runs if isinstance(src.obj.get("paper_metadata"), dict)), None)
    doc = {"paper_metadata": pm, key: final_entries, "evidence": _dedupe_items(items, k)}
    return doc, record_chain


# ── what a follow-up call sees ────────────────────────────────────────────────

def _compact(entry: dict, fields: list[dict], cut: int) -> str:
    out = {}
    for f in fields:
        v = entry.get(f["name"])
        if v is None or isinstance(v, (list, dict)):
            continue
        if isinstance(v, str) and len(v) > cut:
            v = v[:cut].rstrip() + "…"
        out[f["name"]] = v
    return json.dumps(out, ensure_ascii=False)


def render_previous(sources: list[Source], spec: dict, *, budget: int = PREVIOUS_BUDGET) -> tuple[str, bool]:
    """The ``${previous}`` section: every earlier output, entry by entry under stable ids
    (``r1.claims[0]``, ``r1.claims[0].results[2]``, ``adjudicate.variables[1]``), each with
    the quotes behind it. Over budget: quotes go first, then the text gets shorter."""
    k = len([s for s in sources if s.is_run])

    def render(with_quotes: bool, cut: int) -> str:
        lines = ["# PREVIOUS CALLS (generated)", "",
                 f"You did not read the paper. Below are the outputs of {k} independent run{'s' if k != 1 else ''} "
                 f"(r1…r{k}) of the same extraction of this paper" + (", then the earlier follow-up calls" if len(sources) > k else "")
                 + ". Refer to them by the ids shown; never copy their quotes (see EVIDENCE)."]
        for src in sources:
            ent = src.spec.get("entries") or {}
            key = src.key
            fields = ent.get("fields") or []
            ents = entries_of(src.obj, key)
            head = (f"run {src.pass_index + 1} of {k}" if src.is_run and src.pass_index is not None else
                    "run" if src.is_run else "follow-up call")
            lines += ["", f"## {src.label} — {head} — {len(ents)} {ent.get('label', 'entry')}{'s' if len(ents) != 1 else ''}"]
            quotes = src.quotes() if with_quotes else []
            for i, e in enumerate(ents):
                at = f"{key}[{i}]"
                lines.append(f"{src.label}.{at}: {_compact(e, fields, cut)}")
                for q in quotes:
                    if q["field"] == at or (q["field"].startswith(at + ".") and "[" not in q["field"][len(at) + 1:]):
                        where = q["field"][len(at) + 1:] or "entry"
                        lines.append(f"   quoted ({where}): \"{str(q['snippet'])[:160]}\""
                                     + (f" (p.{q['page']}" + (f", {q['source']}" if q.get('source') else "") + ")" if q.get("page") else ""))
                for ch in ent.get("children") or []:
                    for j, row in enumerate(_rows(e.get(ch["key"]))):
                        if not isinstance(row, dict):
                            continue
                        cat = f"{at}.{ch['key']}[{j}]"
                        lines.append(f"{src.label}.{cat}: {_compact(row, ch.get('fields') or [], cut)}")
                        for q in quotes:
                            if q["field"] == cat or q["field"].startswith(cat + "."):
                                lines.append(f"   quoted: \"{str(q['snippet'])[:160]}\"" + (f" (p.{q['page']})" if q.get("page") else ""))
        return "\n".join(lines)

    for with_quotes, cut in ((True, 300), (False, 300), (False, 120), (False, 60)):
        text = render(with_quotes, cut)
        if len(text) <= budget:
            return text, not (with_quotes and cut == 300)
    return text[:budget], True


# ── evidence by reference ─────────────────────────────────────────────────────

def _refs_of(item: dict) -> list[str]:
    r = item.get("ref")
    if r is None and isinstance(item.get("refs"), list):
        r = item["refs"]
    return [x for x in (r if isinstance(r, list) else [r]) if isinstance(x, str)]


def _fields_of(item: dict) -> list[str]:
    f = item.get("field")
    if f is None and isinstance(item.get("fields"), list):
        f = item["fields"]
    return [x for x in (f if isinstance(f, list) else [f]) if isinstance(x, str)]


def validate_refs(obj: dict, cspec: dict, sources: dict[str, Source]) -> tuple[list[dict], list[dict]]:
    """Issues in a follow-up's evidence (``bad_ref, unknown_source, dangling_ref,
    ref_without_field, quoted_directly, dangling_path, unreferenced_row, unreferenced_value``)
    and the clean items ``[{refs: [(source, path)], field}]`` that survive."""
    issues: list[dict] = []
    clean: list[dict] = []
    key = (cspec.get("entries") or {}).get("key", "records")
    cited: set[str] = set()
    for n, item in enumerate(obj.get("evidence") or []):
        if not isinstance(item, dict):
            continue
        at = f"evidence[{n}]"
        if item.get("snippet") is not None or item.get("page") is not None:
            issues.append({"path": at, "code": "quoted_directly", "message": "a follow-up call cites earlier outputs by ref, never a quote; item dropped"})
            continue
        refs = _refs_of(item)
        fields = _fields_of(item)
        if not refs:
            issues.append({"path": f"{at}.ref", "code": "bad_ref", "message": "missing ref"})
            continue
        if not fields:
            issues.append({"path": f"{at}.field", "code": "ref_without_field", "message": "a ref must say which path of the output it supports"})
            continue
        good: list[tuple[str, str]] = []
        for ref in refs:
            m = REF_RE.match(ref.strip())
            if not m:
                issues.append({"path": f"{at}.ref", "code": "bad_ref", "message": f"{ref!r} is not <source>.<key>[i](.<child>[j])(.<field>)"})
                continue
            src = sources.get(m["src"])
            if src is None:
                issues.append({"path": f"{at}.ref", "code": "unknown_source", "message": f"no earlier output is called {m['src']!r}"})
                continue
            if not preset_spec._path_exists(src.obj, m["path"], src.key):
                issues.append({"path": f"{at}.ref", "code": "dangling_ref", "message": f"{ref!r} does not exist in {m['src']}"})
                continue
            good.append((m["src"], _norm_path(m["path"])))
        ok_fields = []
        for fp in fields:
            if preset_spec._path_exists(obj, fp, key):
                ok_fields.append(_norm_path(fp)); cited.add(_norm_path(fp))
            else:
                issues.append({"path": f"{at}.field", "code": "dangling_path", "message": f"{fp!r} does not exist in the output"})
        if good and ok_fields:
            for fp in ok_fields:
                clean.append({"refs": good, "field": fp})
    ent = cspec.get("entries") or {}
    for i, e in enumerate(entries_of(obj, key)):
        at = f"{key}[{i}]"
        under = {c for c in cited if c == at or c.startswith(at + ".")}
        if not under:
            issues.append({"path": at, "code": "unreferenced_row", "message": f"{ent.get('label') or key} cites no earlier output"})
            continue
        if at in under:
            continue                                         # an entry-level ref inherits every value
        for f in ent.get("fields") or []:
            if f.get("evidence") == "value" and e.get(f["name"]) not in (None, "", []) and f"{at}.{f['name']}" not in under:
                issues.append({"path": f"{at}.{f['name']}", "code": "unreferenced_value", "message": "value has no ref"})
    return issues, clean


def _lookup(src: Source, path: str) -> tuple[list[dict], str]:
    """The quotes of ``src`` that cover ``path``: exact, else its row, table or entry
    (``kind`` says which). An entry-level path returns everything under the entry."""
    quotes = src.quotes()
    m = _PATH_RE.match(path)
    if not m:
        return [], "none"
    entry = f"{m['key']}[{m['i']}]"
    if m["child"] is None and m["field"] is None:
        return [q for q in quotes if q["field"] == entry or q["field"].startswith(entry + ".")
                or (not q["field"].startswith(m["key"] + "[") and q.get("entry_index") == int(m["i"]))], "entry"
    tries = [(path, "exact")]
    if m["child"] is not None:
        row = f"{entry}.{m['child']}[{m['j']}]"
        if m["field"] is not None:
            tries.append((row, "row"))
        tries.append((f"{entry}.{m['child']}", "table"))
    tries.append((entry, "entry"))
    for p, kind in tries:
        hit = [q for q in quotes if q["field"] == p]
        if hit:
            return hit, kind
    hit = [q for q in quotes if not q["field"].startswith(m["key"] + "[") and q.get("entry_index") == int(m["i"])]
    return (hit, "entry") if hit else ([], "none")


def resolve(obj: dict, cspec: dict, clean: list[dict], sources: dict[str, Source], *, k: int,
            identity: list[str] | None = None, runs: list[Source] | None = None,
            fields_for_candidates: list[dict] | None = None) -> tuple[dict, dict[int, dict], list[dict]]:
    """Turn a follow-up's by-reference items into real quotes on ITS paths, plus per-entry
    facts (runs behind it, candidates, flags, trace). Returns (canonical document, record
    facts by entry index, resolved items with their run sets — what a later call may cite)."""
    key = (cspec.get("entries") or {}).get("key", "records")
    ents = entries_of(obj, key)
    fields = fields_for_candidates if fields_for_candidates is not None else ((cspec.get("entries") or {}).get("fields") or [])
    items: list[dict] = []
    per_entry: dict[int, dict] = {i: {"runs": set(), "run_entries": {}, "trace": []} for i in range(len(ents))}
    for it in clean:
        target = it["field"]
        tm = _PATH_RE.match(target)
        if not tm or tm["key"] != key:
            continue
        g_i = int(tm["i"])
        if g_i >= len(ents):
            continue
        for src_label, path in it["refs"]:
            src = sources[src_label]
            hits, kind = _lookup(src, path)
            pm = _PATH_RE.match(path)
            if src.is_run:
                per_entry[g_i]["runs"].add(src.label)
            for q in hits:
                runs_ = set(q.get("runs") or {src.label})
                per_entry[g_i]["runs"] |= runs_
                # inherited quote → the matching path of the final entry when the field exists there
                t = target
                if tm["child"] is None and tm["field"] is None:
                    entry_src = f"{pm['key']}[{pm['i']}]" if pm else None
                    rest = q["field"][len(entry_src):] if entry_src and q["field"].startswith(entry_src) else ""
                    fm = re.match(r"^\.([A-Za-z_]\w*)", rest)
                    if fm and fm.group(1) in ents[g_i]:
                        t = f"{key}[{g_i}].{fm.group(1)}"
                items.append({"snippet": q["snippet"], "page": q["page"], "orig": q.get("source"),
                              "field": t, "runs": runs_})
            # which run entries stand behind this final entry (for candidates)
            if pm is not None:
                for lab in (set().union(*[set(q.get("runs") or {src.label}) for q in hits]) if hits else ({src.label} if src.is_run else set())):
                    rsrc = sources.get(lab)
                    if rsrc is not None and rsrc.is_run:
                        if src.is_run:
                            re_ = entries_of(rsrc.obj, rsrc.key)
                            if int(pm["i"]) < len(re_):
                                per_entry[g_i]["run_entries"][lab] = re_[int(pm["i"])]
            per_entry[g_i]["trace"].append({
                "field": target, "ref": f"{src_label}.{path}", "kind": kind,
                "source": {"label": src.label, "document_id": src.document_id, "pass_index": src.pass_index,
                           "is_run": src.is_run},
                "pages": sorted({q["page"] for q in hits if q.get("page") is not None}), "n_quotes": len(hits)})
    # an entry no ref touched: find it in the runs by identity, and say so
    if runs and identity:
        by_key = {}
        for r in runs:
            for e in entries_of(r.obj, r.key):
                kk = identity_key(e, identity)
                if kk is not None:
                    by_key.setdefault(kk, []).append((r, e))
        for g_i, e in enumerate(ents):
            if per_entry[g_i]["runs"]:
                continue
            kk = identity_key(e, identity)
            for r, re_ in by_key.get(kk, []) if kk is not None else []:
                per_entry[g_i]["runs"].add(r.label)
                per_entry[g_i]["run_entries"][r.label] = re_
                per_entry[g_i]["trace"].append({"field": f"{key}[{g_i}]", "ref": None, "kind": "identity",
                                                "source": {"label": r.label, "document_id": r.document_id,
                                                           "pass_index": r.pass_index, "is_run": True}})
    record_chain: dict[int, dict] = {}
    for g_i, e in enumerate(ents):
        pe = per_entry[g_i]
        cands, flags = _entry_facts(e, sorted(pe["run_entries"].items()), fields)
        n = len(pe["runs"])
        if not pe["runs"]:
            flags.append("unreferenced")
        elif 2 * n <= k:
            flags.append("minority")
        if any(t["kind"] == "identity" for t in pe["trace"]):
            flags.append("by_identity")
        run_rows = []
        for lab in sorted(pe["runs"], key=lambda x: (len(x), x)):
            rsrc = sources.get(lab)
            run_rows.append({"label": lab, "pass_index": getattr(rsrc, "pass_index", None),
                             "document_id": getattr(rsrc, "document_id", None),
                             "entry_index": next((i for i, x in enumerate(entries_of(rsrc.obj, rsrc.key))
                                                  if x is pe["run_entries"].get(lab)), None) if rsrc else None})
        record_chain[g_i] = {"kind": "prompt", "support": {"n": n, "k": k}, "flags": flags,
                             "candidates": cands, "runs": run_rows, "trace": pe["trace"]}
    pm_ = obj.get("paper_metadata")
    if not isinstance(pm_, dict):
        pm_ = next((s.obj.get("paper_metadata") for s in sources.values() if s.is_run and isinstance(s.obj.get("paper_metadata"), dict)), None)
    final_entries = [{k_: v for k_, v in e.items() if k_ != "evidence"} for e in ents]
    doc = {"paper_metadata": pm_, key: final_entries, "evidence": _dedupe_items(items, k)}
    resolved = [{**it, "runs": set(it["runs"])} for it in items]
    return doc, record_chain, resolved


def match_runs(obj: dict, runs: list[Source], spec: dict, identity: list[str]) -> dict[int, dict]:
    """Per-entry facts for a document whose entries were produced elsewhere (an imported
    aggregate without refs): runs holding the same identity, candidates, flags."""
    ent = spec.get("entries") or {}
    key = ent.get("key", "records")
    fields = ent.get("fields") or []
    k = len(runs)
    out: dict[int, dict] = {}
    for g_i, e in enumerate(entries_of(obj, key)):
        kk = identity_key(e, identity)
        hits: list[tuple[str, dict, Source, int]] = []
        for r in runs:
            for i, re_ in enumerate(entries_of(r.obj, r.key)):
                if kk is not None and identity_key(re_, identity) == kk:
                    hits.append((r.label, re_, r, i)); break
        cands, flags = _entry_facts(e, [(lab, re_) for lab, re_, _, _ in hits], fields)
        n = len(hits)
        if n and 2 * n <= k:
            flags.append("minority")
        if not n:
            flags.append("unreferenced")
        out[g_i] = {"kind": "identity", "support": {"n": n, "k": k}, "flags": flags, "candidates": cands,
                    "runs": [{"label": lab, "pass_index": r.pass_index, "document_id": r.document_id, "entry_index": i}
                             for lab, _, r, i in hits], "trace": None}
    return out


def external_meta(obj: dict, spec: dict) -> dict[int, dict] | None:
    """What an externally aggregated document already says about its runs (a ``runs``
    child, support and flags fields), as the engine's record facts."""
    mapping = preset_spec.chain_external(spec)
    if mapping is None:
        return None
    key = (spec.get("entries") or {}).get("key", "records")
    pm = obj.get("paper_metadata") if isinstance(obj.get("paper_metadata"), dict) else {}
    k = pm.get("k") if isinstance(pm.get("k"), int) else None
    labels = pm.get("runs") if isinstance(pm.get("runs"), list) else None
    out: dict[int, dict] = {}
    for g_i, e in enumerate(entries_of(obj, key)):
        rows = [r for r in _rows(e.get(mapping.get("child"))) if isinstance(r, dict)] if mapping.get("child") else []
        run_label = mapping.get("run")
        support = None
        sv = e.get(mapping["support"]) if mapping.get("support") else None
        m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", str(sv or ""))
        if m:
            support = {"n": int(m.group(1)), "k": int(m.group(2))}
        elif rows:
            support = {"n": len(rows), "k": k or (len(labels) if labels else len(rows))}
        fl = e.get(mapping["flags"]) if mapping.get("flags") else None
        flags = [str(x) for x in fl] if isinstance(fl, list) else ([str(fl)] if fl not in (None, "", []) else [])
        cands: dict[str, list[dict]] = {}
        for ef, cf in (mapping.get("fields") or {}).items():
            vals = [(str(r.get(run_label) if run_label else j), r.get(cf)) for j, r in enumerate(rows) if r.get(cf) not in (None, "", [])]
            c = _candidates(vals)
            if len(c) > 1:
                cands[ef] = c
        out[g_i] = {"kind": "external", "support": support, "flags": flags, "candidates": cands,
                    "runs": [{"label": str(r.get(run_label) if run_label else j), "row_index": j} for j, r in enumerate(rows)],
                    "trace": None}
    return out


# ── running a chain ───────────────────────────────────────────────────────────

def _default_generate(prompt: str, *, model: str, api_key: str, base_url: str | None = None) -> str:
    """One text-only call, asking once more for bare JSON when the first answer was not."""
    from . import providers
    raw = ""
    for nudge in ("", "\n\nReturn ONLY the JSON object described above."):
        raw = providers.generate_text(model, api_key, prompt + nudge, temperature=0, base_url=base_url,
                                      max_tokens=16384, json_mode=True)
        if isinstance(parse_result_json(raw), dict):
            break
    return raw


def spans_of(conn, document_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT entry_index, field_path, snippet, page, source FROM evidence_span "
        "WHERE document_id = %s::uuid ORDER BY ord", (document_id,)).fetchall()
    return [{"entry_index": ei, "field_path": fp, "snippet": sn, "page": pg, "source": src}
            for (ei, fp, sn, pg, src) in rows]


def runs_needed(k: int) -> int:
    """How many of k runs must succeed for the paper to land: all of one, else at least half
    and at least two."""
    return 1 if k <= 1 else max(2, math.ceil(k / 2))


def run_chain(conn, pdf_bytes: bytes, prompt: str, *, plan: dict, spec: dict, params: dict | None = None,
              model: str = "", api_key: str = "", base_url: str | None = None, use_text: bool = False,
              schema_id: str | None = None, session_id: str | None = None, owner_user_id: str | None = None,
              source_job_id: str | None = None, filename: str | None = None, prompt_edited: bool = False,
              entries_key: str | None = None, complete: Callable | None = None,
              generate: Callable | None = None, progress: Callable | None = None, store=None) -> dict:
    """k runs of the preset's extraction, then its follow-up calls, then ONE final document
    persisted through the ordinary path. Returns ``run_extraction``'s result plus
    ``group_id``, ``run_document_ids``, ``n_calls`` (what was actually charged) and
    ``fallback`` (set when a follow-up failed and the runs were voted instead)."""
    from . import extract as _extract, providers
    store = store or storage.get_store()
    progress = progress or (lambda _s: None)
    complete = complete or _extract._default_complete
    generate = generate or _default_generate
    group_id = str(uuid.uuid4())
    k, calls = plan["k"], plan["calls"]
    common = dict(model=model, api_key=api_key, base_url=base_url, use_text=use_text, schema_id=schema_id,
                  session_id=session_id, owner_user_id=owner_user_id, source_job_id=source_job_id,
                  filename=filename, params=params, entries_key=entries_key, spec=spec,
                  prompt_edited=prompt_edited, store=store)
    captured: dict = {}

    def capturing(pdf_b, prompt_, **kw):
        res = complete(pdf_b, prompt_, **kw)
        captured["last"] = res
        return res

    runs: list[Source] = []
    prov: list[dict] = []
    failed: list[dict] = []
    texts: list[str] = []
    try:
        for i in range(k):
            progress(f"run {i + 1} of {k}")
            last_exc: Exception | None = None
            for attempt in (1, 2):
                try:
                    res = _extract.run_extraction(conn, pdf_bytes, prompt, complete=capturing,
                                                  role="run", group_id=group_id, pass_index=i,
                                                  store_artifacts=False, **common)
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001 - a run may fail; the chain decides
                    last_exc = exc
                    if attempt == 1 and providers.is_transient(exc) and RETRY_DELAY:
                        time.sleep(RETRY_DELAY)
                        continue
                    break
            if last_exc is not None:
                failed.append({"pass_index": i, "error": providers.extract_provider_message(last_exc)[:300]})
                continue
            llm = captured["last"]
            obj = parse_result_json(llm.text)
            runs.append(Source(label=f"r{i + 1}", obj=obj if isinstance(obj, dict) else {}, spec=spec,
                               spans=spans_of(conn, res["document_id"]), document_id=res["document_id"],
                               pass_index=i))
            texts.append(llm.text or "")
            prov.append({"id": f"r{i + 1}", "kind": "extract", "pass_index": i, "document_id": res["document_id"],
                         "model": model, "resolved_model": llm.resolved_model, "usage": llm.usage,
                         "finish_reason": llm.finish_reason, "prompt_sha256": _sha(prompt), "n_issues": res.get("n_issues", 0)})
        if len(runs) < runs_needed(k):
            raise RuntimeError(f"{len(failed)} of {k} extraction runs failed"
                               + (f": {failed[0]['error']}" if failed else ""))

        sources: dict[str, Source] = {r.label: r for r in runs}
        previous: list[Source] = list(runs)
        fallback: dict | None = None
        final_doc: dict | None = None
        record_chain: dict[int, dict] | None = None
        all_issues: list[dict] = []
        trace: dict = {}
        for n, call in enumerate(calls):
            cspec = preset_spec.spec_for_call(spec, call)
            prev_text, truncated = render_previous(previous, spec)
            text = preset_spec.render_call_prompt(spec, call, params, prev_text)
            progress(f"{call['id']} ({n + 1} of {len(calls)})")
            raw = ""
            try:
                raw = generate(text, model=call.get("model") or model, api_key=api_key, base_url=base_url)
                obj = parse_result_json(raw)
                if not isinstance(obj, dict):
                    raise ValueError("the follow-up call did not return a JSON object")
            except Exception as exc:  # noqa: BLE001 - the runs stand; the paper still lands
                fallback = {"call": call["id"], "error": providers.extract_provider_message(exc)[:300]}
                prov.append({"id": call["id"], "kind": "prompt", "model": call.get("model") or model,
                             "prompt_sha256": preset_spec.call_recipe_sha(spec, call, params),
                             "truncated_previous": truncated, "error": fallback["error"]})
                texts.append(raw or "")
                break
            issues = [x for x in preset_spec.validate_result(obj, cspec, params)
                      if x["code"] not in ("bad_page", "uncited_value", "uncited_row")
                      and not (x["path"] == "paper_metadata" and x["code"] == "missing")]
            ref_issues, clean = validate_refs(obj, cspec, sources)
            is_last = n == len(calls) - 1
            doc, rc, resolved = resolve(obj, cspec, clean, sources, k=len(runs),
                                        identity=plan.get("identity") if is_last else None, runs=runs,
                                        fields_for_candidates=((spec.get("entries") or {}).get("fields") if is_last else None))
            src = Source(label=call["id"], obj=obj, spec=cspec, items=resolved, is_run=False)
            sources[call["id"]] = src
            previous.append(src)
            texts.append(raw)
            prov.append({"id": call["id"], "kind": "prompt", "model": call.get("model") or model,
                         "prompt_sha256": preset_spec.call_recipe_sha(spec, call, params),
                         "truncated_previous": truncated, "n_issues": len(issues) + len(ref_issues)})
            all_issues += [{**x, "call": call["id"]} for x in issues + ref_issues]
            if is_last:
                final_doc, record_chain = doc, rc
                trace = {str(i): v.get("trace") for i, v in rc.items()}
        if final_doc is None:
            final_doc, record_chain = vote(runs, spec, plan)
            if fallback:
                for v in record_chain.values():
                    v["flags"] = list(v.get("flags") or []) + ["fallback"]
                    v["kind"] = "vote"
        provenance = {"k": k, "n_runs": len(runs), "identity": plan.get("identity") or [],
                      "aggregate": plan.get("aggregate"), "calls": prov, "n_calls": len(prov),
                      "failed_runs": failed, "fallback": fallback,
                      "chain_sha256": _sha(json.dumps([_sha(prompt), k, plan.get("aggregate"),
                                                        [c.get("prompt_sha256") for c in prov if c["kind"] == "prompt"]],
                                                       sort_keys=True))}
        final_text = json.dumps(final_doc, ensure_ascii=False)
        usage = _sum_usage([c.get("usage") for c in prov])
        resolved_model = next((c.get("resolved_model") for c in prov if c.get("resolved_model")), None)

        def supplied(pdf_b, prompt_, **kw):
            return _extract.LLMResult(text=final_text, finish_reason="stop", usage=usage, resolved_model=resolved_model)

        progress("merging")
        res = _extract.run_extraction(conn, pdf_bytes, prompt, complete=supplied, role="final",
                                      group_id=group_id, chain=provenance, record_chain=record_chain, **common)
        if all_issues:                       # the follow-ups' structural issues join the final's
            cur = conn.execute("SELECT issues FROM extraction_document WHERE id = %s::uuid", (res["document_id"],)).fetchone()
            merged = list((cur[0] if cur and isinstance(cur[0], list) else []) or []) + all_issues
            from psycopg.types.json import Json
            with conn.transaction():
                conn.execute("UPDATE extraction_document SET issues = %s WHERE id = %s::uuid", (Json(merged), res["document_id"]))
    except Exception:
        records.delete_group(conn, group_id)
        raise
    final_id = res["document_id"]
    for i, t in enumerate(texts):
        try:
            store.put(storage.call_raw_key(final_id, i), t.encode("utf-8"), "text/plain; charset=utf-8")
        except Exception:  # noqa: BLE001 - debugging copies never fail a run
            pass
    try:
        store.put(storage.chain_key(final_id), json.dumps(
            {"group_id": group_id, "final_id": final_id, "calls": prov, "trace": trace,
             "runs": [{"label": r.label, "document_id": r.document_id, "pass_index": r.pass_index} for r in runs]},
            ensure_ascii=False, default=str).encode("utf-8"), "application/json")
    except Exception:  # noqa: BLE001
        pass
    return {**res, "group_id": group_id, "run_document_ids": [r.document_id for r in runs],
            "n_calls": len(prov), "fallback": fallback}


# ── importing a chain that ran outside ────────────────────────────────────────

def is_chain_payload(obj: Any) -> bool:
    return isinstance(obj, dict) and isinstance(obj.get("passes"), list) and bool(obj["passes"]) \
        and isinstance(obj.get("aggregate"), dict)


def import_chain(conn, payload: dict, *, spec: dict, schema_id: str | None, pdf_bytes: bytes | None,
                 session_id: str | None, owner_user_id: str | None, filename: str | None,
                 produced_by: str | None = None, store=None,
                 passes_spec: dict | None = None, passes_schema_id: str | None = None) -> dict:
    """``{passes: [doc, …], aggregate: doc}``: the passes become runs, the aggregate the final.
    An aggregate citing by ref is resolved against the passes; one carrying quotes is kept
    and matched to the passes by identity. The passes may have another shape than the
    aggregate (claims behind a graph of edges, say): ``passes_spec`` / ``passes_schema_id``
    read and file them, the aggregate keeps ``spec``. Without a PDF nothing is located, but
    the group, the facts and the trace are the same."""
    from . import extract as _extract
    from .ingest import ingest
    store = store or storage.get_store()
    group_id = str(uuid.uuid4())
    pspec = passes_spec or spec
    psid = passes_schema_id or schema_id
    made_by = (produced_by or "").strip()[:200] or "imported"

    def persist_doc(obj: dict, *, role: str, pass_index: int | None, chain: dict | None = None,
                    record_chain: dict | None = None) -> str:
        dspec, dsid = (pspec, psid) if role == "run" else (spec, schema_id)
        text = json.dumps(obj, ensure_ascii=False)
        if pdf_bytes:
            def supplied(pdf_b, prompt_, **kw):
                return _extract.LLMResult(text=text, finish_reason="stop", usage={}, resolved_model=made_by)
            res = _extract.run_extraction(conn, pdf_bytes, "", schema_id=dsid, session_id=session_id,
                                          owner_user_id=owner_user_id, filename=filename, complete=supplied,
                                          spec=dspec, role=role, group_id=group_id, pass_index=pass_index,
                                          chain=chain, record_chain=record_chain, store_artifacts=role != "run",
                                          store=store)
            return res["document_id"]
        doc_id = records.persist(conn, ingest(text, entries_key=(dspec.get("entries") or {}).get("key")), schema_id=dsid,
                                 session_id=session_id, owner_user_id=owner_user_id, filename=filename, role=role,
                                 group_id=group_id, pass_index=pass_index, chain=chain, record_chain=record_chain)
        try:
            store.put(storage.raw_key(doc_id), text.encode("utf-8"), "text/plain; charset=utf-8")
        except Exception:  # noqa: BLE001
            pass
        return doc_id

    runs: list[Source] = []
    prov: list[dict] = []
    try:
        for i, p in enumerate(payload["passes"]):
            obj = parse_result_json(p)
            if not isinstance(obj, dict):
                raise ValueError(f"passes[{i}] is not a JSON object")
            did = persist_doc(obj, role="run", pass_index=i)
            runs.append(Source(label=f"r{i + 1}", obj=obj, spec=pspec, spans=spans_of(conn, did), document_id=did, pass_index=i))
            prov.append({"id": f"r{i + 1}", "kind": "extract", "pass_index": i, "document_id": did, "model": made_by})
        agg = parse_result_json(payload["aggregate"])
        if not isinstance(agg, dict):
            raise ValueError("aggregate is not a JSON object")
        sources = {r.label: r for r in runs}
        ident = list(((spec.get("chain") or {}).get("identity")) or [])
        by_ref = any(isinstance(it, dict) and (it.get("ref") or it.get("refs")) for it in agg.get("evidence") or [])
        if by_ref:
            issues, clean = validate_refs(agg, spec, sources)
            # which of the aggregate's fields the runs can disagree about: when the passes have
            # their own shape, only the fields the preset maps onto the runs (a field that merely
            # shares a name — the edge's "3/3" support vs a claim's support grade — is no vote)
            afields = (spec.get("entries") or {}).get("fields") or []
            if pspec is not spec:
                mapped = set(((preset_spec.chain_external(spec) or {}).get("fields") or {}).keys()) | set(ident)
                afields = [f for f in afields if f["name"] in mapped]
            final_doc, record_chain, _ = resolve(agg, spec, clean, sources, k=len(runs), identity=ident, runs=runs,
                                                 fields_for_candidates=afields)
        else:
            final_doc, record_chain, issues = agg, (external_meta(agg, spec) or match_runs(agg, runs, spec, ident)), []
        provenance = {"k": len(runs), "n_runs": len(runs), "identity": ident, "aggregate": {"kind": "external"},
                      "calls": prov + [{"id": "aggregate", "kind": "external", "model": made_by}], "n_calls": 0,
                      "failed_runs": [], "fallback": None, "imported": True,
                      **({"passes_schema_id": psid} if psid != schema_id else {})}
        final_id = persist_doc(final_doc, role="final", pass_index=None, chain=provenance, record_chain=record_chain)
        if issues:
            from psycopg.types.json import Json
            cur = conn.execute("SELECT issues FROM extraction_document WHERE id = %s::uuid", (final_id,)).fetchone()
            with conn.transaction():
                conn.execute("UPDATE extraction_document SET issues = %s WHERE id = %s::uuid",
                             (Json(list((cur[0] if cur and isinstance(cur[0], list) else []) or []) + issues), final_id))
    except Exception:
        records.delete_group(conn, group_id)
        raise
    paper_id = conn.execute("SELECT paper_id FROM extraction_document WHERE id = %s::uuid", (final_id,)).fetchone()[0]
    n_records = conn.execute("SELECT count(*) FROM record WHERE document_id = %s::uuid", (final_id,)).fetchone()[0]
    return {"document_id": final_id, "paper_id": str(paper_id) if paper_id else None, "schema_id": schema_id,
            "n_records": int(n_records), "group_id": group_id, "run_document_ids": [r.document_id for r in runs]}
