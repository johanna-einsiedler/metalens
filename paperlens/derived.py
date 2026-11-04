"""Dataset-level DERIVE steps of a workflow: a unit of rows computed over the records of an
extract step — a taxonomy of the claims, the edges of a corpus graph, the columns per table —
by a chain of calls (a model call that reads the rows, a deterministic ``group`` transform),
reviewed as a draft next to the committed version, frozen into releases beside the untouched
per-paper records.

What a derived row is: the output fields the step declares, plus three engine-owned ones —
``_id`` (a hash of the identity fields, stable across versions), ``sources`` (the record ids
the row was made from; a row with none is dropped, the way a page section without a source is)
and, on review, a decision. An UPDATE run sees the committed rows (``${existing}``) and the
current input rows with the records added since marked ``[NEW]``; the review shows every row
as kept / revised / new / removed with the new records that explain the change, computed by
the engine from the rows, never taken from the model's say-so (``pages.py`` is the precedent).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter

from psycopg.types.json import Json

from . import analysis_table, contract, records
from . import preset_spec as ps

BUDGET_CHARS = 60_000          # the rows one model call may read, serialised
MAX_CALLS = 24                 # per step and run: chunks × model calls
RESERVED = ("_id", "sources", "_status", "_decision")
RULES = ("count", "n_papers", "first", "majority", "union", "all", "sum", "mean", "min", "max")
FILTER_OPS = ("eq", "ne", "in", "not_in", "empty", "not_empty")
PLACEHOLDERS = ("rows", "existing", "previous", "output_schema", "dataset", "new_count")
_PH_RE = re.compile(r"\$\{([a-z_]+)\}")
_SYS_IN = ("_record_id", "_path", "_paper", "_study", "_new")

_COLS = ("id::text, dataset_id::text, step, version, status, rows, change_log, decisions, model, prompt_sha256, step_sha, built_from, "
         "proposal, owner_user_id::text, run_id::text, created_at, committed_at")


def _row(r) -> dict:
    return {"id": r[0], "dataset_id": r[1], "step": r[2], "version": r[3], "status": r[4], "rows": r[5] or [], "change_log": r[6] or [],
            "decisions": r[7] or {}, "model": r[8], "prompt_sha256": r[9], "step_sha": r[10], "built_from": r[11] or {}, "proposal": r[12] or {},
            "owner_user_id": r[13], "run_id": r[14], "created_at": r[15].isoformat(timespec="seconds") if r[15] else None,
            "committed_at": r[16].isoformat(timespec="seconds") if r[16] else None}


# ── the declaration ────────────────────────────────────────────────────────────

def steps_for(spec: dict) -> list[dict]:
    return [st for st in (spec or {}).get("steps") or [] if isinstance(st, dict) and st.get("kind") == "derive"]


def step_sha(st: dict) -> str:
    """The whole step (prompt wording included): a changed declaration makes the derived unit stale."""
    return hashlib.sha256(ps.canonical_json(st).encode("utf-8")).hexdigest()[:16]


def output_fields(st: dict) -> list[dict]:
    return [f for f in ((st.get("output") or {}).get("fields") or []) if isinstance(f, dict) and isinstance(f.get("name"), str)]


def llm_calls(st: dict) -> list[dict]:
    return [c for c in st.get("calls") or [] if isinstance(c, dict) and c.get("kind") == "llm"]


def placeholders(text: str) -> set[str]:
    return set(_PH_RE.findall(text or ""))


def input_columns_of(pspec: dict | None, st: dict, vocab_columns: list[str] | None = None) -> list[str] | None:
    """The columns the step's input unit offers (None when the unit does not exist)."""
    unit = next((u for u in analysis_table.units(pspec) if u["id"] == (st.get("input") or {}).get("unit", "entries")), None)
    if unit is None:
        return None
    cols = [c["name"] for c in analysis_table._column_list(pspec, unit)]   # noqa: SLF001
    for v in vocab_columns or []:
        cols += [f"{v}_concept", f"{v}_polarity"]
    return cols


def validate(st: dict, at: str, E, W, *, pspec: dict | None, vocab_columns: list[str] | None) -> None:
    """The rules of a derive step beyond the grammar: its input against the extract step's
    preset, its calls' placeholders and transforms, its output fields and identity."""
    inp = st.get("input") if isinstance(st.get("input"), dict) else {}
    cols = input_columns_of(pspec, st, vocab_columns) if pspec else None
    unit = inp.get("unit", "entries")
    if pspec and cols is None:
        E(f"{at}.input.unit: {unit!r} is not a unit of the extract step's preset")
    for k in inp:
        if k not in ("unit", "columns", "filter", "paper_fields"):
            E(f"{at}.input: unknown key {k!r}")
    if cols is not None:
        for c in inp.get("columns") or []:
            if c not in cols:
                E(f"{at}.input.columns: {c!r} is not a column of the {unit!r} unit")
        for j, f in enumerate(inp.get("filter") or []):
            if not isinstance(f, dict) or f.get("column") not in cols or f.get("op") not in FILTER_OPS:
                E(f"{at}.input.filter[{j}] must be {{column: <a column>, op: {'|'.join(FILTER_OPS)}, value?}}")
        gb = st.get("group_by")
        if gb is not None and gb not in cols:
            E(f"{at}.group_by: {gb!r} is not a column of the input")
    outs = {f["name"] for f in output_fields(st)}
    for f in output_fields(st):
        if f.get("type") == "enum" and not (isinstance(f.get("options"), list) and f["options"]):
            E(f"{at}.output.fields[{f['name']}]: an enum names its options")
    calls = [c for c in st.get("calls") or [] if isinstance(c, dict)]
    prev_fields: set[str] | None = set(cols or []) if cols is not None else None
    for j, c in enumerate(calls):
        cat = f"{at}.calls[{j}]"
        if c.get("kind") == "llm":
            text = (c.get("prompt") or {}).get("text") or ""
            ph = placeholders(text)
            for p in ph - set(PLACEHOLDERS):
                E(f"{cat}.prompt: unknown placeholder ${{{p}}}")
            if j == 0 and "rows" not in ph:
                E(f"{cat}.prompt must read ${{rows}} (the first call sees the input rows)")
            if j > 0 and "previous" not in ph:
                E(f"{cat}.prompt must read ${{previous}} (a later call sees the earlier call's rows)")
            if j > 0 and "rows" in ph:
                W(f"{cat}.prompt reads ${{rows}} again: the input rows are sent twice")
            prev_fields = set(outs) | {"_study", "_paper"}          # a model's row carries its first source's paper
        elif c.get("kind") == "transform":
            by = c.get("by")
            if not isinstance(by, list) or not by or any(not isinstance(b, str) for b in by):
                E(f"{cat}.by must list the columns a group is keyed by"); by = []
            elif prev_fields is not None:
                for b in by:
                    if b not in prev_fields:
                        E(f"{cat}.by: {b!r} is not a column of what the transform reads")
            for b in by:
                if b not in outs:
                    E(f"{cat}.by: {b!r} must also be an output field (a group's key is carried through)")
            fields = c.get("fields")
            if fields is not None and not isinstance(fields, dict):
                E(f"{cat}.fields must map output fields to rules ({', '.join(RULES)})")
            for name, rule in (fields or {}).items() if isinstance(fields, dict) else []:
                op, col = (rule, name) if isinstance(rule, str) else ((rule or {}).get("op"), (rule or {}).get("column", name)) if isinstance(rule, dict) else (None, name)
                if name not in outs:
                    E(f"{cat}.fields: {name!r} is not an output field")
                if op not in RULES:
                    E(f"{cat}.fields.{name}: rule must be one of {', '.join(RULES)}")
                elif op not in ("count", "n_papers") and prev_fields is not None and col not in prev_fields:
                    E(f"{cat}.fields.{name}: {col!r} is not a column of what the transform reads")
            prev_fields = set(outs)
    if calls and calls[-1].get("kind") == "llm" and not st.get("identity"):
        E(f"{at}.identity is required (the rows of several calls are merged on it)")


# ── the input rows ─────────────────────────────────────────────────────────────

def _passes(val, f: dict) -> bool:
    op, want = f.get("op"), f.get("value")
    empty = val in (None, "", [])
    if op == "empty":
        return empty
    if op == "not_empty":
        return not empty
    vals = val if isinstance(val, list) else [val]
    if op == "eq":
        return any(v == want for v in vals)
    if op == "ne":
        return all(v != want for v in vals)
    if op == "in":
        return any(v in (want or []) for v in vals)
    if op == "not_in":
        return all(v not in (want or []) for v in vals)
    return True


def input_rows(conn, dataset_id: str, st: dict, *, release: dict | None = None, new_ids: set[str] | None = None) -> dict:
    """{rows, columns, records, papers}: the input unit's rows projected to the declared
    columns, each with its record id, cell path, paper index and whether the record is new."""
    inp = st.get("input") if isinstance(st.get("input"), dict) else {}
    t = analysis_table.build(conn, dataset_id, inp.get("unit", "entries"), owner=True, release=release, crosscheck=False, vocabulary=True)
    if t is None:
        return {"rows": [], "columns": [], "records": [], "papers": []}
    names = [c["name"] for c in t["columns"]]
    want = [c for c in (inp.get("columns") or []) if c in names] or [n for n in names if not n.startswith("_") or n == "_study"]
    if "_study" not in want:
        want.append("_study")
    idx = {n: k for k, n in enumerate(names)}
    filters = [f for f in (inp.get("filter") or []) if isinstance(f, dict) and f.get("column") in idx]
    out = []
    new_ids = new_ids or set()
    for row in t["rows"]:
        v = row["v"]
        if any(not _passes(v[idx[f["column"]]], f) for f in filters):
            continue
        rec = t["records"][row["r"]]
        item = {c: v[idx[c]] for c in want}
        item.update({"_record_id": rec["id"], "_path": row["p"], "_paper": rec["paper"], "_new": rec["id"] in new_ids})
        out.append(item)
    return {"rows": out, "columns": want, "records": t["records"], "papers": t["papers"]}


def current_record_ids(conn, dataset_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT id::text FROM record WHERE dataset_id = %s::uuid AND NOT COALESCE(screened_empty, false) ORDER BY id", (dataset_id,)).fetchall()]


def new_since(conn, dataset_id: str, old_built_from: dict | None) -> set[str]:
    now = set(current_record_ids(conn, dataset_id))
    old = set((old_built_from or {}).get("record_ids") or [])
    return now - old if old else now


# ── identity, transforms, validation of what a call returns ────────────────────

def _norm(v) -> str:
    s = " ".join(str(v if v is not None else "").split()).lower()
    return s.strip(" .,;:!?\"'")


def identity_id(row: dict, identity: list[str]) -> str:
    return hashlib.sha1("|".join(_norm(row.get(f)) for f in identity).encode("utf-8")).hexdigest()[:12]   # noqa: S324 - an id, not a secret


def _num(v):
    return analysis_table.num(v)


def transform_group(rows: list[dict], call: dict, st: dict) -> list[dict]:
    """``op: group`` — one output row per distinct ``by`` key, the other output fields by rule."""
    by = [b for b in call.get("by") or [] if isinstance(b, str)]
    rules = call.get("fields") if isinstance(call.get("fields"), dict) else {}
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for r in rows:
        key = tuple(_norm(r.get(b)) for b in by)
        if key not in groups:
            groups[key] = []; order.append(key)
        groups[key].append(r)
    outs = [f["name"] for f in output_fields(st)]
    out = []
    for key in order:
        members = groups[key]
        row: dict = {}
        for b in by:
            row[b] = next((m.get(b) for m in members if m.get(b) not in (None, "")), None)
        for name in outs:
            if name in by:
                continue
            rule = rules.get(name)
            if rule is None:
                continue
            op, col = (rule, name) if isinstance(rule, str) else ((rule or {}).get("op"), (rule or {}).get("column", name))
            vals = [m.get(col) for m in members]
            present = [v for v in vals if v not in (None, "", [])]
            if op == "count":
                row[name] = len(members)
            elif op == "n_papers":
                row[name] = len({m.get("_paper") if m.get("_paper") is not None else m.get("_study") for m in members})
            elif op == "first":
                row[name] = present[0] if present else None
            elif op == "majority":
                flat = [x for v in present for x in (v if isinstance(v, list) else [v])]
                row[name] = Counter(json.dumps(x, sort_keys=True) for x in flat).most_common(1)[0][0] if flat else None
                row[name] = json.loads(row[name]) if row[name] is not None else None
            elif op == "union":
                seen: list = []
                for v in present:
                    for x in (v if isinstance(v, list) else [v]):
                        if x not in seen:
                            seen.append(x)
                row[name] = seen
            elif op == "all":
                row[name] = [x for v in present for x in (v if isinstance(v, list) else [v])]
            elif op in ("sum", "mean", "min", "max"):
                nums = [n for n in (_num(v) for v in present) if n is not None]
                row[name] = (None if not nums else sum(nums) if op == "sum" else sum(nums) / len(nums) if op == "mean"
                             else min(nums) if op == "min" else max(nums))
        srcs: list[str] = []
        for m in members:
            for s in (m.get("sources") or ([m["_record_id"]] if m.get("_record_id") else [])):
                if s not in srcs:
                    srcs.append(s)
        row["sources"] = srcs
        row["_paper"], row["_study"] = members[0].get("_paper"), members[0].get("_study")
        out.append(row)
    return out


def validate_rows(rows, st: dict, allowed_ids: set[str], *, aliases: dict[str, str] | None = None, merge: bool = True) -> tuple[list[dict], list[str]]:
    """What a model (or a transform) returned, made honest: unknown keys dropped, types coerced,
    enum values repaired, required-empty rows dropped, sources resolved (aliases → record ids)
    and checked against the admissible set, a row without a source dropped, ``_id`` set from
    the identity, duplicate ids merged (first wins, sources united) — unless ``merge`` is off,
    for a call whose rows a later transform still groups (one row per claim, say)."""
    repairs: list[str] = []
    fields = output_fields(st)
    identity = [x for x in st.get("identity") or [] if isinstance(x, str)]
    aliases = aliases or {}
    out: list[dict] = []
    seen: dict[str, dict] = {}
    for i, r in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(r, dict):
            continue
        clean: dict = {}
        for f in fields:
            v = r.get(f["name"])
            t = f.get("type", "string")
            if v in ("", None):
                v = None
            elif t in ("integer", "number"):
                n = _num(v)
                if n is None:
                    repairs.append(f"row {i}: {f['name']} {v!r} is not a number — cleared"); v = None
                else:
                    v = int(n) if t == "integer" and float(n).is_integer() else n
            elif t == "boolean":
                v = v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1")
            elif t in ("list", "multi"):
                v = v if isinstance(v, list) else [x.strip() for x in str(v).split(";") if x.strip()]
            elif t == "enum":
                opts = [o.get("value") if isinstance(o, dict) else o for o in f.get("options") or []]
                if v not in opts:
                    hit = next((o for o in opts if _norm(o) == _norm(v)), None)
                    if hit is None:
                        repairs.append(f"row {i}: {f['name']} {v!r} is not an option — cleared"); v = None
                    else:
                        v = hit
            else:
                v = str(v) if not isinstance(v, (list, dict)) else json.dumps(v, ensure_ascii=False)
            clean[f["name"]] = v
        if any(clean.get(f["name"]) in (None, []) for f in fields if f.get("required")):
            repairs.append(f"row {i} dropped: a required field is empty"); continue
        srcs = []
        for s in (r.get("sources") or []):
            s = aliases.get(s, s) if isinstance(s, str) else None
            if s in allowed_ids and s not in srcs:
                srcs.append(s)
        bad = [s for s in (r.get("sources") or []) if not (isinstance(s, str) and aliases.get(s, s) in allowed_ids)]
        if bad:
            repairs.append(f"row {i}: dropped unknown sources {bad[:3]}")
        if not srcs:
            repairs.append(f"row {i} dropped: no admissible source"); continue
        clean["sources"] = srcs
        clean["_id"] = identity_id(clean, identity) if identity else hashlib.sha1(json.dumps(clean, sort_keys=True).encode()).hexdigest()[:12]   # noqa: S324
        for k in ("_paper", "_study"):
            if r.get(k) is not None:
                clean[k] = r[k]
        if merge and clean["_id"] in seen:
            prev = seen[clean["_id"]]
            prev["sources"] = prev["sources"] + [s for s in srcs if s not in prev["sources"]]
            repairs.append(f"row {i}: same identity as an earlier row — merged")
            continue
        seen[clean["_id"]] = clean
        out.append(clean)
    return out, repairs


# ── the chain: prompts, chunks, calls ──────────────────────────────────────────

def output_schema(st: dict) -> str:
    lines = []
    for f in output_fields(st):
        t = f.get("type", "string")
        extra = ""
        if t == "enum":
            extra = " one of " + " | ".join(str(o.get("value") if isinstance(o, dict) else o) for o in f.get("options") or [])
        lines.append(f'  "{f["name"]}": <{t}{extra}>{" (required)" if f.get("required") else ""}' + (f"  — {f['help']}" if f.get("help") else ""))
    return "{\n" + ",\n".join(lines) + ',\n  "sources": ["<row id>", "…"]\n}'


def _serialise(rows: list[dict], aliases: dict[str, str]) -> list[str]:
    """One compact line per input row under a short alias (r1, r2, …): a model cites aliases,
    not 36-character ids."""
    back = {v: k for k, v in aliases.items()}
    out = []
    for r in rows:
        rid = r.get("_record_id")
        alias = back.get(rid)
        if alias is None:
            alias = f"r{len(aliases) + 1}"; aliases[alias] = rid; back[rid] = alias
        body = {k: v for k, v in r.items() if not k.startswith("_") and v not in (None, "", [])}
        out.append(f"- {alias}{' [NEW]' if r.get('_new') else ''}: " + json.dumps(body, ensure_ascii=False)[:1500])
    return out


def _existing_text(existing: list[dict]) -> str:
    if not existing:
        return "(no committed rows yet)"
    return "\n".join("- " + json.dumps({k: v for k, v in r.items() if k not in ("_decision",)}, ensure_ascii=False)[:1500] for r in existing)


def render_call(call: dict, st: dict, *, rows_text: str, existing_text: str, previous_text: str, dataset_title: str, new_count: int) -> str:
    text = (call.get("prompt") or {}).get("text") or ""
    fill = {"rows": rows_text, "existing": existing_text, "previous": previous_text, "output_schema": output_schema(st),
            "dataset": dataset_title, "new_count": str(new_count)}
    body = _PH_RE.sub(lambda m: fill.get(m.group(1), m.group(0)), text)
    tail = ["", "## Return ONLY this JSON object", "", '{"rows": [ <one object per row, each in this shape> ', output_schema(st), "],",
            ' "change_log": [{"op": "keep|revise|add|drop", "old_id": "<_id of a committed row or null>", "because": ["<row id>"]}]}', "",
            "Every row MUST cite the ids of the rows it was made from in `sources` (r1, r2, … as listed); a row without a source is dropped.",
            "With committed rows given, return the full set: unchanged rows verbatim with their _id, and a change_log naming the NEW rows that caused each revise/add/drop."]
    return body + "\n".join(tail)


def plan(rows: list[dict], st: dict) -> dict:
    """How many model calls the step makes over these rows: the rows are serialised; over the
    budget they are split by ``group_by`` (default: per paper), groups whole, greedily filled."""
    n_llm = len(llm_calls(st))
    if not n_llm:
        return {"n_rows": len(rows), "n_chunks": 1, "n_calls": 0, "llm_calls": 0, "chunks": [rows]}
    gb = st.get("group_by") or "_paper"
    lines = _serialise(rows, {})
    total = sum(len(x) + 1 for x in lines)
    if total <= BUDGET_CHARS:
        chunks = [rows]
    else:
        groups: dict = {}
        for r, line in zip(rows, lines):
            groups.setdefault(r.get(gb), []).append((r, len(line) + 1))
        chunks, cur, size = [], [], 0
        for members in groups.values():
            gsize = sum(s for _r, s in members)
            if cur and size + gsize > BUDGET_CHARS:
                chunks.append(cur); cur, size = [], 0
            cur += [r for r, _s in members]; size += gsize
        if cur:
            chunks.append(cur)
    return {"n_rows": len(rows), "n_chunks": len(chunks), "n_calls": len(chunks) * n_llm, "llm_calls": n_llm, "chunks": chunks,
            "over_budget": len(chunks) * n_llm > MAX_CALLS}


def _default_generate(model: str, api_key: str, base_url: str | None):
    from . import providers
    def gen(prompt: str) -> str:
        return providers.generate_text(model, api_key, prompt, base_url=base_url, temperature=0, max_tokens=16384, json_mode=True)
    return gen


def run_chain(st: dict, inp: dict, existing: list[dict], *, dataset_title: str, generate=None, new_ids: set[str] | None = None) -> dict:
    """Execute the calls over the input rows: {rows, change_log, repairs, n_calls, prompt_sha256,
    raw}. A transform reads the input rows (first call) or the previous call's rows; a model
    call reads a chunk of the input rows (first) or the previous rows (later), the chunks'
    answers concatenated and, after the last model call, merged on the identity."""
    rows_in = inp["rows"]
    allowed = {r["_record_id"] for r in rows_in if r.get("_record_id")}
    identity = [x for x in st.get("identity") or [] if isinstance(x, str)]
    pl = plan(rows_in, st)
    if pl.get("over_budget"):
        raise ValueError(f"the step would make {pl['n_calls']} model calls (the limit is {MAX_CALLS}): narrow the input or add a transform first")
    previous: list[dict] | None = None
    repairs: list[str] = []
    change_log: list[dict] = []
    raws: list[str] = []
    n_calls = 0
    shas: list[str] = []
    ex_text = _existing_text(existing)
    for j, call in enumerate(st.get("calls") or []):
        if call.get("kind") == "transform":
            src = rows_in if previous is None else previous
            previous = transform_group(src, call, st)
            continue
        if generate is None:
            raise ValueError("a model call needs a model")
        chunks = pl["chunks"] if previous is None else [previous]
        got: list[dict] = []
        for chunk in chunks:
            aliases: dict[str, str] = {}
            if previous is None:
                rows_text = "\n".join(_serialise(chunk, aliases))
                prev_text = ""
            else:
                rows_text = "\n".join(_serialise(rows_in, aliases)) if "rows" in placeholders((call.get("prompt") or {}).get("text") or "") else ""
                prev_text = "\n".join("- " + json.dumps(r, ensure_ascii=False)[:1500] for r in chunk)
                for r in rows_in:                       # a later call cites the input rows' aliases too
                    rid = r.get("_record_id")
                    if rid and rid not in aliases.values():
                        aliases[f"r{len(aliases) + 1}"] = rid
            prompt = render_call(call, st, rows_text=rows_text, existing_text=ex_text, previous_text=prev_text, dataset_title=dataset_title,
                                 new_count=sum(1 for r in rows_in if r.get("_new")))
            shas.append(hashlib.sha256(prompt.encode("utf-8")).hexdigest())
            parsed, raw = None, ""
            for nudge in ("", "\n\nReturn ONLY the JSON object described above."):
                raw = generate(prompt + nudge)
                n_calls += 1
                parsed = contract.parse_result_json(raw)
                if isinstance(parsed, dict) and isinstance(parsed.get("rows"), list):
                    break
                if isinstance(parsed, list):
                    parsed = {"rows": parsed}; break
            raws.append((raw or "")[:4000])
            usable = isinstance(parsed, dict) and isinstance(parsed.get("rows"), list)
            if not usable:
                raise ValueError("the model answered, but no rows came back")
            last = j == len(st.get("calls") or []) - 1
            clean, reps = validate_rows(parsed["rows"], st, allowed, aliases=aliases, merge=last and len(chunks) == 1)
            repairs += [f"call {call.get('id', j)}: {x}" for x in reps]
            for r in clean:                                  # the paper of the first source, for n_papers / union of studies
                first = next((x for x in rows_in if x.get("_record_id") == (r.get("sources") or [None])[0]), None)
                if first:
                    r["_paper"], r["_study"] = first.get("_paper"), first.get("_study")
            got += clean
            for e in parsed.get("change_log") or []:
                if isinstance(e, dict) and e.get("op") in ("keep", "revise", "add", "drop"):
                    change_log.append({"op": e["op"], "old_id": e.get("old_id") if isinstance(e.get("old_id"), str) else None,
                                       "because": [aliases.get(x, x) for x in (e.get("because") or []) if isinstance(x, str)]})
        if len(chunks) > 1 and identity and j == len(st.get("calls") or []) - 1:   # the chunks' answers merged on the identity
            merged, reps = validate_rows(got, st, allowed)
            repairs += [x for x in reps if "merged" in x]
            got = merged
        previous = got
    final, reps = validate_rows(previous or [], st, allowed)
    repairs += reps
    final = [{k: v for k, v in r.items() if k not in ("_paper", "_study")} for r in final]
    return {"rows": final, "change_log": change_log, "repairs": repairs, "n_calls": n_calls,
            "prompt_sha256": hashlib.sha256("".join(shas).encode()).hexdigest() if shas else None, "raw": raws}


# ── the computed review ────────────────────────────────────────────────────────

def row_diff(old_rows: list[dict], new_rows: list[dict], *, new_ids: set[str]) -> list[dict]:
    """Per new row a verdict against the committed rows (by ``_id``): kept | revised | new, the
    fields that changed, and its sources split into new-since-the-commit and carried; removed
    committed rows appended. ``unexplained``: revised or new without a new record among its sources."""
    old_by = {r.get("_id"): r for r in old_rows or []}
    used: set[str] = set()
    out = []
    for r in new_rows or []:
        o = old_by.get(r.get("_id"))
        if o is None:
            verdict, changed = "new", []
        else:
            used.add(r["_id"])
            changed = [k for k in r if k not in ("sources", "_id", "_decision") and r.get(k) != o.get(k)]
            if set(r.get("sources") or []) != set(o.get("sources") or []):
                changed.append("sources")
            verdict = "revised" if changed else "kept"
        srcs = r.get("sources") or []
        out.append({"verdict": verdict, "_id": r.get("_id"), "row": r, "old": o, "changed": changed,
                    "sources_new": [s for s in srcs if s in new_ids], "sources_carried": [s for s in srcs if s not in new_ids],
                    "unexplained": verdict in ("revised", "new") and bool(new_ids) and not any(s in new_ids for s in srcs)})
    for rid, o in old_by.items():
        if rid not in used:
            out.append({"verdict": "removed", "_id": rid, "row": o, "old": o, "changed": [], "sources_new": [], "sources_carried": list(o.get("sources") or []),
                        "unexplained": False})
    return out


# ── storage ────────────────────────────────────────────────────────────────────

def list_for_dataset(conn, dataset_id: str, step: str | None = None) -> list[dict]:
    return [_row(r) for r in conn.execute(
        f"SELECT {_COLS} FROM dataset_derived WHERE dataset_id = %s::uuid AND (%s::text IS NULL OR step = %s) ORDER BY step, version",
        (dataset_id, step, step)).fetchall()]


def get(conn, did: str) -> dict | None:
    if not records._is_uuid(did):   # noqa: SLF001
        return None
    r = conn.execute(f"SELECT {_COLS} FROM dataset_derived WHERE id = %s::uuid", (did,)).fetchone()
    return _row(r) if r else None


def committed(conn, dataset_id: str, step: str) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM dataset_derived WHERE dataset_id = %s::uuid AND step = %s AND status = 'committed' ORDER BY version DESC LIMIT 1",
                     (dataset_id, step)).fetchone()
    return _row(r) if r else None


def latest_draft(conn, dataset_id: str, step: str) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM dataset_derived WHERE dataset_id = %s::uuid AND step = %s AND status = 'draft' ORDER BY version DESC LIMIT 1",
                     (dataset_id, step)).fetchone()
    return _row(r) if r else None


def create(conn, dataset_id: str, *, step: str, rows: list[dict], change_log: list[dict], model: str | None, prompt_sha256: str | None,
           step_sha_: str, built_from: dict, proposal: dict | None, owner_user_id: str | None, run_id: str | None) -> dict:
    did = str(uuid.uuid4())
    with conn.transaction():
        n = conn.execute("SELECT coalesce(max(version), 0) FROM dataset_derived WHERE dataset_id = %s::uuid AND step = %s", (dataset_id, step)).fetchone()[0]
        conn.execute("DELETE FROM dataset_derived WHERE dataset_id = %s::uuid AND step = %s AND status = 'draft'", (dataset_id, step))
        conn.execute(
            """INSERT INTO dataset_derived (id, dataset_id, step, version, status, rows, change_log, model, prompt_sha256, step_sha, built_from, proposal, owner_user_id, run_id)
               VALUES (%s::uuid, %s::uuid, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s::uuid)""",
            (did, dataset_id, step, n + 1, Json(rows), Json(change_log), model, prompt_sha256, step_sha_, Json(built_from), Json(proposal or {}),
             owner_user_id if records._is_uuid(owner_user_id or "") else None, run_id))   # noqa: SLF001
    return get(conn, did)


def update_draft(conn, did: str, *, rows: list[dict] | None = None, decisions: dict | None = None) -> dict | None:
    with conn.transaction():
        n = conn.execute(
            "UPDATE dataset_derived SET rows = coalesce(%s::jsonb, rows), decisions = coalesce(%s::jsonb, decisions) WHERE id = %s::uuid AND status = 'draft'",
            (Json(rows) if rows is not None else None, Json(decisions) if decisions is not None else None, did)).rowcount
    return get(conn, did) if n else None


def commit(conn, did: str) -> dict | None:
    """Commit the draft: rows the reviewer rejected are dropped from the committed rows."""
    d = get(conn, did)
    if d is None or d["status"] != "draft":
        return None
    rejected = {k for k, v in (d.get("decisions") or {}).items() if v == "reject"}
    rows = [r for r in d["rows"] if r.get("_id") not in rejected]
    with conn.transaction():
        conn.execute("UPDATE dataset_derived SET status = 'committed', committed_at = now(), rows = %s WHERE id = %s::uuid AND status = 'draft'",
                     (Json(rows), did))
    return get(conn, did)


def delete(conn, did: str) -> int:
    with conn.transaction():
        return conn.execute("DELETE FROM dataset_derived WHERE id = %s::uuid AND status = 'draft'", (did,)).rowcount


def snapshot(conn, dataset_id: str, spec: dict | None = None) -> list[dict]:
    """The committed derived units, newest version per step — what a release freezes, with the
    step's declaration beside the rows."""
    best: dict[str, dict] = {}
    for r in list_for_dataset(conn, dataset_id):
        if r["status"] == "committed" and (r["step"] not in best or r["version"] > best[r["step"]]["version"]):
            best[r["step"]] = r
    by_step = {st["id"]: st for st in steps_for(spec or {})}
    out = []
    for step, r in sorted(best.items()):
        out.append({"step": step, "version": r["version"], "committed_at": r["committed_at"], "model": r["model"], "prompt_sha256": r["prompt_sha256"],
                    "step_sha": r["step_sha"], "run_id": r["run_id"], "built_from": {k: v for k, v in r["built_from"].items() if k != "record_ids"},
                    "declaration": by_step.get(step), "rows": r["rows"]})
    return out


# ── running a step ─────────────────────────────────────────────────────────────

def basis_for(conn, dataset_id: str) -> dict:
    from . import workflows
    b = workflows.basis(conn, dataset_id)
    return {**b, "record_ids": current_record_ids(conn, dataset_id)}


def run_step(conn, dataset_id: str, st: dict, *, model: str | None, api_key: str | None, base_url: str | None = None, generate=None,
             run_id: str | None = None, owner_user_id: str | None = None) -> dict:
    """Make the next draft of the step: {draft, n_calls, repairs}. ``generate`` overrides the
    model call (tests); a transform-only step needs no model."""
    d = records.get_dataset(conn, dataset_id) or {}
    old = committed(conn, dataset_id, st["id"])
    new_ids = new_since(conn, dataset_id, (old or {}).get("built_from")) if old else set()
    inp = input_rows(conn, dataset_id, st, new_ids=new_ids)
    gen = generate
    if gen is None and llm_calls(st):
        if not model or (not api_key and not base_url):
            raise ValueError("a model call needs a model and a key")
        gen = _default_generate(model, api_key or "", base_url)
    res = run_chain(st, inp, (old or {}).get("rows") or [], dataset_title=d.get("title") or "", generate=gen, new_ids=new_ids)
    built = basis_for(conn, dataset_id)
    built["n_new"] = len(new_ids)
    built["input"] = {"unit": (st.get("input") or {}).get("unit", "entries"), "n_rows": len(inp["rows"]), "columns": inp["columns"]}
    built["derived_version_from"] = old["version"] if old else None
    draft = create(conn, dataset_id, step=st["id"], rows=res["rows"], change_log=res["change_log"], model=model if llm_calls(st) else None,
                   prompt_sha256=res["prompt_sha256"], step_sha_=step_sha(st), built_from=built,
                   proposal={"repairs": res["repairs"], "raw": res["raw"], "n_calls": res["n_calls"]}, owner_user_id=owner_user_id, run_id=run_id)
    return {"draft": draft, "n_calls": res["n_calls"], "repairs": res["repairs"]}


def state(conn, dataset_id: str, st: dict) -> dict:
    """fresh | stale | missing | draft, why, and the committed / draft versions."""
    from . import workflows
    com = committed(conn, dataset_id, st["id"])
    draft = latest_draft(conn, dataset_id, st["id"])
    item: dict = {"version": com["version"] if com else None, "draft_id": draft["id"] if draft else None,
                  "derived_id": com["id"] if com else None, "committed_rows": len(com["rows"]) if com else 0}
    if draft:
        item["state"] = "draft"; item["because"] = [f"draft v{draft['version']} awaits review"]
        return item
    if com is None:
        item["state"] = "missing"; item["because"] = ["never made"]
        return item
    now = workflows.basis(conn, dataset_id)
    was = com.get("built_from") or {}
    if was.get("record_ids_sha") != now["record_ids_sha"]:
        item["state"] = "stale"; item["because"] = ["records added or removed since it was made"]
    elif (was.get("events_max") or "") != (now.get("events_max") or ""):
        item["state"] = "stale"; item["because"] = ["corrections after it was made"]
    elif com.get("step_sha") != step_sha(st):
        item["state"] = "stale"; item["because"] = ["the step's declaration changed"]
    else:
        item["state"] = "fresh"; item["because"] = []
    return item


def review(conn, did: str) -> dict | None:
    """The draft (or a committed version) as the change it proposes against the committed one
    before it, with the labels of the records it cites."""
    d = get(conn, did)
    if d is None:
        return None
    prev = conn.execute(f"SELECT {_COLS} FROM dataset_derived WHERE dataset_id = %s::uuid AND step = %s AND status = 'committed' AND version < %s ORDER BY version DESC LIMIT 1",
                        (d["dataset_id"], d["step"], d["version"])).fetchone()
    prev = _row(prev) if prev else None
    new_ids = set()
    if prev:
        now = set((d.get("built_from") or {}).get("record_ids") or [])
        new_ids = now - set((prev.get("built_from") or {}).get("record_ids") or [])
    diff = row_diff((prev or {}).get("rows") or [], d["rows"], new_ids=new_ids)
    cited = sorted({s for r in d["rows"] for s in r.get("sources") or []} | {s for r in (prev or {}).get("rows") or [] for s in r.get("sources") or []})
    labels = {}
    if cited:
        for rid, doc, ei, title, authors, year in conn.execute(
                """SELECT r.id::text, r.document_id::text, r.entry_index, p.title, p.authors, p.year FROM record r LEFT JOIN paper p ON p.id = r.paper_id
                   WHERE r.id = ANY(%s::uuid[])""", (cited,)).fetchall():
            labels[rid] = {"document_id": doc, "entry_index": ei, "label": f"{analysis_table._study_label(authors, year, title)} · #{ei + 1}"}   # noqa: SLF001
    return {"derived": d, "committed": {k: prev[k] for k in ("id", "version", "committed_at")} if prev else None, "diff": diff, "labels": labels,
            "repairs": (d.get("proposal") or {}).get("repairs") or [], "new_ids": sorted(new_ids),
            "summary": dict(Counter(x["verdict"] for x in diff)) | {"unexplained": sum(1 for x in diff if x["unexplained"])}}


# ── the derived unit as a table (dashboards, exports) ──────────────────────────

def units_for(conn, dataset_id: str, spec: dict | None, *, release: dict | None = None) -> list[dict]:
    """The committed derived units of the dataset (or the ones frozen in a release)."""
    if release:
        snap = (release.get("snapshot") or {}).get("derived") or []
        return [{"id": x["step"], "label": ((x.get("declaration") or {}).get("output") or {}).get("label") or (x.get("declaration") or {}).get("label") or x["step"],
                 "level": "derived", "default": False, "n": len(x.get("rows") or [])} for x in snap]
    out = []
    by_step = {st["id"]: st for st in steps_for(spec or {})}
    for x in snapshot(conn, dataset_id, spec):
        st = by_step.get(x["step"]) or x.get("declaration") or {}
        out.append({"id": x["step"], "label": (st.get("output") or {}).get("label") or st.get("label") or x["step"], "level": "derived", "default": False,
                    "n": len(x["rows"])})
    return out


def table(conn, dataset_id: str, step: str, *, owner: bool, spec: dict | None = None, release: dict | None = None) -> dict | None:
    """The derived unit in the analysis-table wire shape: ``_id`` + the output fields + the
    primary source's paper columns + the source counts; ``records``/``papers`` are the source
    records, each row pointing at its primary source (``r``) and listing its sources (``src``)."""
    if release:
        snap = next((x for x in (release.get("snapshot") or {}).get("derived") or [] if x["step"] == step), None)
        st = (snap or {}).get("declaration") or {}
        rows_d = (snap or {}).get("rows") or []
        version = (snap or {}).get("version")
    else:
        com = committed(conn, dataset_id, step)
        if com is None:
            return None
        st = next((s for s in steps_for(spec or {}) if s["id"] == step), None) or {}
        rows_d, version = com["rows"], com["version"]
    if not st:
        return None
    base = analysis_table.build(conn, dataset_id, (st.get("input") or {}).get("unit", "entries"), owner=owner, release=release, crosscheck=False, vocabulary=False)
    if base is None:
        return None
    rec_idx = {r["id"]: k for k, r in enumerate(base["records"])}
    cols = [{"name": "_id", "label": "Row id", "type": "string", "scope": "system", "help": "a hash of the identity fields, stable across versions", "options": None},
            *[{"name": f["name"], "label": f.get("label") or f["name"].replace("_", " "), "type": f.get("type") or "string", "scope": "derived",
               "help": f.get("help") or "", "options": f.get("options") if isinstance(f.get("options"), list) else None} for f in output_fields(st)],
            *[dict(c) for c in analysis_table.SYSTEM_COLUMNS if c["name"] in ("_study", "_title", "_doi", "_year", "_journal")],
            {"name": "_n_sources", "label": "Source records", "type": "integer", "scope": "system", "help": "how many records the row was made from", "options": None},
            {"name": "_n_papers", "label": "Source papers", "type": "integer", "scope": "system", "help": "how many papers those records come from", "options": None}]
    wire = []
    for r in rows_d:
        srcs = [s for s in r.get("sources") or [] if s in rec_idx]
        if not srcs:
            continue
        prim = base["records"][rec_idx[srcs[0]]]
        paper = base["papers"][prim["paper"]]
        vals = {"_id": r.get("_id"), **{f["name"]: r.get(f["name"]) for f in output_fields(st)},
                "_study": paper.get("study"), "_title": paper.get("title"), "_doi": paper.get("doi"), "_year": paper.get("year"), "_journal": paper.get("journal"),
                "_n_sources": len(srcs), "_n_papers": len({base["records"][rec_idx[s]]["paper"] for s in srcs})}
        wire.append({"r": rec_idx[srcs[0]], "p": "", "v": [vals.get(c["name"]) for c in cols], "src": [rec_idx[s] for s in srcs]})
    for c in cols:
        present = [row["v"][cols.index(c)] for row in wire if row["v"][cols.index(c)] not in (None, "", [])]
        c["n"] = len(present); c["distinct"] = len({json.dumps(v, sort_keys=True, default=str) for v in present})
        c["samples"] = [v if not isinstance(v, str) else v[:60] for v in present[:5]]
        c["roles"] = ["identifier"] if c["name"] == "_id" else ["measure"] if c["type"] in ("number", "integer") else ["dimension"] if c["distinct"] <= 40 else ["label"]
    out = {**{k: base[k] for k in ("dataset", "papers", "records", "as_of", "viewer")},
           "unit": {"id": step, "label": (st.get("output") or {}).get("label") or st.get("label") or step, "level": "derived"},
           "units": base["units"] + units_for(conn, dataset_id, spec, release=release), "n_rows": len(wire), "truncated": False,
           "columns": cols, "rows": wire, "derived": {"step": step, "version": version, "identity": st.get("identity"),
                                                       "calls": [{k: c.get(k) for k in ("id", "kind", "op")} for c in st.get("calls") or [] if isinstance(c, dict)]}}
    return out


def cell_evidence(conn, dataset_id: str, step: str, rows: list[dict], *, release: dict | None = None) -> dict:
    """For the export: per derived row the quotes of its source records (one per record — the
    entry-level or first quote), as {row index: {"_sources": {"k": "sources", "items": [...]}}}."""
    base = analysis_table.build(conn, dataset_id, "entries", owner=False, release=release, crosscheck=False, vocabulary=False)
    if base is None:
        return {}
    ids = [base["records"][i]["id"] for row in rows for i in row.get("src") or [] if i < len(base["records"])]
    if not ids:
        return {}
    if release:
        ev = [(e["rec"], e["field_path"], e["snippet"], e["page"], e["source"]) for e in (release.get("snapshot") or {}).get("evidence") or [] if e["rec"] in set(ids)]
    else:
        ev = conn.execute("SELECT record_id::text, field_path, snippet, page, source FROM evidence_span WHERE record_id = ANY(%s::uuid[])", (ids,)).fetchall()
    first: dict[str, dict] = {}
    for rid, fp, snippet, page, source in sorted(ev, key=lambda e: (e[0], len(e[1] or ""), e[1] or "")):
        if rid not in first and snippet:
            first[rid] = {"record_id": rid, "snippet": snippet, "page": page, "source": source}
    out = {}
    for n, row in enumerate(rows):
        items = [first[base["records"][i]["id"]] for i in row.get("src") or [] if i < len(base["records"]) and base["records"][i]["id"] in first]
        if items:
            out[str(n)] = {"_sources": {"k": "sources", "items": items}}
    return out
