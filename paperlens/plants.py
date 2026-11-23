"""PLANTED ERRORS: attention checks in a review task worked on a table (a template block with
``layout: "flags"``, paperlens/templates.py).

A template that says ``plants: {share, seed, kinds}`` shows about ``share`` of a coder's papers with
one deliberate error: a cited value altered (two digits swapped, the last digit moved, the sign
flipped, the stars changed: ``value``) or one part of a cell's mapping swapped for a part another
cell of the same exhibit has (an outcome, a subgroup: ``mapping``). Whether a paper gets one is
drawn from the seed, the run, the step, the paper and the coder — the same draw every time — and
never once the coder has answered anything on the paper (no plant appears in work already done).

The record is never touched. The coder's view carries the plant beside the data
(``task.shown``: the unit, the field, the value to show) and, for a value, in the exhibit's
transcription too; the workspace draws the altered value or mapping from it and never saves it
(a paper table is saved whole from the record's values, so the plant cannot leak into a coding).
The plant is CAUGHT when the coder's first answer to its question (``grid.flag``'s for a value,
``grid.mapping``'s for a mapping) is not the answer ``confirm`` gives, MISSED when it is, OPEN
before. A catch is told at once (``task.caught``; the cell is shown as it truly is); a miss is
not. The owner reads every plant with its status (``GET /api/workflow-runs/{id}/plants``, the
gate's ``plants`` line, the plants on a paper in the owner's view); analyses leave the planted
units out.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

import psycopg

from . import templates as T

KINDS = ("value", "mapping")


# ── the template's say ─────────────────────────────────────────────────────────────────────

def config(tpl: dict | None) -> tuple[dict, dict] | None:
    """(plants, the flags block they act on), or None for a template without plants."""
    cfg = (tpl or {}).get("plants")
    if not isinstance(cfg, dict):
        return None
    for st in tpl.get("stages") or []:
        for bl in (st or {}).get("blocks") or []:
            if isinstance(bl, dict) and bl.get("layout") == "flags" and isinstance(bl.get("grid"), dict):
                return cfg, bl
    return None


def _u(*parts: Any) -> float:
    """A uniform number in [0, 1) from its parts: the same parts, the same number."""
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return int(h[:15], 16) / float(16 ** 15)


# ── the alterations ─────────────────────────────────────────────────────────────────────────

_NUM = re.compile(r"^(\s*[\(\[]?)([-−–]?)(\d+)(?:\.(\d+))?(\**)([\)\]]?\s*)$")


def perturb_value(s: Any, h: float) -> str | None:
    """A printed number made plausibly wrong — two adjacent digits swapped, the last digit moved
    by one, the sign flipped (not on a zero), the stars changed — the choice by ``h``. None when
    ``s`` is no number as printed (or every change would leave it as it is)."""
    m = _NUM.match(str(s if s is not None else ""))
    if not m:
        return None
    pre, sign, ip, fp, stars, post = m.groups()
    digits = ip + (fp or "")
    cands: list[str] = []

    def build(sign_: str, digits_: str, stars_: str) -> str:
        ip_, fp_ = digits_[:len(ip)], digits_[len(ip):]
        return f"{pre}{sign_}{ip_}{('.' + fp_) if fp is not None else ''}{stars_}{post}"

    for i in range(len(digits) - 1):
        if digits[i] != digits[i + 1]:
            d = list(digits)
            d[i], d[i + 1] = d[i + 1], d[i]
            if len(ip) > 1 and d[0] == "0":
                continue                                            # no leading zero on a whole number
            cands.append(build(sign, "".join(d), stars))
    last = digits[-1]
    cands.append(build(sign, digits[:-1] + (str(int(last) + 1) if last != "9" else "8"), stars))
    if any(c != "0" for c in digits):
        cands.append(build("" if sign else "-", digits, stars))
    if stars:
        cands.append(build(sign, digits, {1: "**", 2: "***", 3: "**"}.get(len(stars), "*")))
    cands = [c for c in dict.fromkeys(cands) if c != str(s)]
    return cands[int(h * len(cands)) % len(cands)] if cands else None


def perturb_mapping(text: Any, others: list[Any], sep: str, h: float) -> str | None:
    """The mapping in words with one part swapped for the part another cell of the exhibit has in
    that place (another outcome, another subgroup), the choice by ``h``; None when every other
    cell agrees with this one in every part."""
    parts = [p.strip() for p in str(text or "").split(sep)]
    if not any(parts):
        return None
    alts: list[tuple[int, str]] = []
    for k, p in enumerate(parts):
        seen = []
        for o in others:
            op = [x.strip() for x in str(o or "").split(sep)]
            if k < len(op) and op[k] and op[k] != p and op[k] not in seen:
                seen.append(op[k])
        alts.extend((k, v) for v in seen)
    if not alts:
        return None
    k, v = alts[int(h * len(alts)) % len(alts)]
    return sep.join(v if i == k else p for i, p in enumerate(parts))


# ── the transcription (an exhibit as markdown, as static/grammar.js parses it) ─────────────────

def _norm(s: Any) -> str:
    return re.sub(r"^panel\s+", "", re.sub(r"\s+", " ", str(s if s is not None else "")).strip().lower())


def locate_cell(text: str, panel: Any, row: Any, column: Any) -> tuple[int, int] | None:
    """(line index, cell index) of a cell in a transcription — by its panel's label (with several
    panels), its row label and its column header, as the grid places it; None when not found."""
    panels: list[dict] = []
    labels: list[str] = []
    cur: dict | None = None
    caption = False
    for i, raw in enumerate(str(text or "").split("\n")):
        t = raw.strip()
        if re.match(r"^\|.*\|$", t):
            if cur is None:
                cur = {"labels": labels, "header": None, "rows": []}
                panels.append(cur)
                labels = []
            if re.match(r"^\|[\s:|-]+\|$", t):
                if cur["header"] is None and len(cur["rows"]) == 1:
                    cur["header"] = cur["rows"].pop(0)[1]
                continue
            cur["rows"].append((i, [c.strip() for c in t[1:-1].split("|")]))
            continue
        cur = None
        if not t:
            continue
        b = re.match(r"^\*\*(.+)\*\*$", t)
        if b:
            if not caption and not panels and not labels:
                caption = True
            else:
                labels.append(b.group(1).strip())
    want = _norm(panel)
    pis = list(range(len(panels)))
    if len(panels) > 1 and want:
        pis = [i for i in pis if any(_norm(x) == want for x in panels[i]["labels"])]
    for pi in pis:
        p = panels[pi]
        if not p["header"]:
            continue
        ci = next((j for j, h in enumerate(p["header"]) if j > 0 and _norm(h) == _norm(column)), None)
        ri = next((k for k, (_, r) in enumerate(p["rows"]) if r and _norm(r[0]) == _norm(row)), None)
        if ci and ri is not None:
            return p["rows"][ri][0], ci
    return None


def replace_cell(text: str, line: int, ci: int, value: str) -> str:
    lines = str(text).split("\n")
    raw = lines[line]
    lead = raw[:len(raw) - len(raw.lstrip())]
    cells = raw.strip()[1:-1].split("|")
    cells[ci] = f" {value} "
    lines[line] = lead + "|" + "|".join(cells) + "|"
    return "\n".join(lines)


# ── drawing a plant ─────────────────────────────────────────────────────────────────────────

def choose(cfg: dict, block: dict, units: list[dict], key: tuple) -> dict | None:
    """The plant for one coder's paper among the block's units (``templates.unit_rows``), or None.
    ``key`` (seed, run, step, document, coder) makes every choice the same each time."""
    g = block.get("grid") or {}
    kinds = [k for k in (cfg.get("kinds") or list(KINDS)) if k in KINDS]
    value_q, map_g = (g.get("flag") or {}).get("question"), g.get("mapping") or {}
    if "value" in kinds and not (g.get("value") and value_q):
        kinds.remove("value")
    if "mapping" in kinds and not (map_g.get("field") and map_g.get("question") and map_g.get("sep")):
        kinds.remove("mapping")
    if not kinds or not units:
        return None
    start = int(_u(*key, "kind") * len(kinds))
    for kind in kinds[start:] + kinds[:start]:
        field = g["value"] if kind == "value" else map_g["field"]
        cands = [u for u in units if (u.get("row") or {}).get(field) not in (None, "")]
        if not cands:
            continue
        first = int(_u(*key, kind, "unit") * len(cands))
        for u in cands[first:] + cands[:first]:
            orig = (u.get("row") or {}).get(field)
            if kind == "value":
                new = perturb_value(orig, _u(*key, kind, u["key"], "how"))
            else:
                new = perturb_mapping(orig, [(x.get("row") or {}).get(field) for x in cands if x is not u], map_g["sep"], _u(*key, kind, u["key"], "how"))
            if new and new != orig:
                return {"unit": u["key"], "kind": kind, "field": field, "question": value_q if kind == "value" else map_g["question"],
                        "original": str(orig), "planted": new}
    return None


_COLS = "id::text, run_id::text, step_run_id::text, step_id, document_id::text, coder_user_id::text, template, unit, kind, field, question, original, planted, created_at"
_KEYS = [c.split("::")[0].strip() for c in _COLS.split(",")]


def _row(r) -> dict:
    return dict(zip(_KEYS, r))


def get(conn: psycopg.Connection, step_run_id: str, document_id: str, coder_user_id: str) -> dict | None:
    r = conn.execute(f"SELECT {_COLS} FROM review_plant WHERE step_run_id = %s::uuid AND document_id = %s::uuid AND coder_user_id = %s::uuid",
                     (step_run_id, document_id, coder_user_id)).fetchone()
    return _row(r) if r else None


def ensure(conn: psycopg.Connection, view: dict, tpl: dict, pspec: dict, sampled: dict | None, run: dict, step_run: dict,
           document_id: str, coder_user_id: str) -> dict | None:
    """The coder's plant on this paper: the one stored, else drawn now (and stored) — unless the
    template has none, the draw says no, or the coder has already answered on the paper."""
    have = get(conn, step_run["id"], document_id, coder_user_id)
    if have is not None:
        return have
    cb = config(tpl)
    if cb is None:
        return None
    cfg, block = cb
    key = (cfg.get("seed") or "plants", run["id"], step_run["step_id"], document_id, coder_user_id)
    if _u(*key, "draw") >= float(cfg.get("share") or 0):
        return None
    from . import coders
    started = coders.answers_get(conn, document_id, coder_user_id, tpl["id"])
    if started.get("paper") or started.get("records"):
        return None
    plant = choose(cfg, block, T.unit_rows(block, view, pspec, sampled=sampled), key)
    if plant is None:
        return None
    with conn.transaction():
        conn.execute("""INSERT INTO review_plant (id, run_id, step_run_id, step_id, document_id, coder_user_id, template, unit, kind, field, question, original, planted)
                        VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (step_run_id, document_id, coder_user_id) DO NOTHING""",
                     (str(uuid.uuid4()), run["id"], step_run["id"], step_run["step_id"], document_id, coder_user_id, tpl["id"],
                      plant["unit"], plant["kind"], plant["field"], plant["question"], plant["original"], plant["planted"]))
    return get(conn, step_run["id"], document_id, coder_user_id)


# ── caught or missed ────────────────────────────────────────────────────────────────────────

def _verdict(tpl: dict | None, plant: dict, first: Any) -> str:
    if first is None:
        return "open"
    cb = config(tpl)
    ok = ((cb[1].get("confirm") or {}).get("answers") or {}) if cb else {}
    if plant["question"] in ok:
        return "missed" if str(first) == str(ok[plant["question"]]) else "caught"
    g = (cb[1].get("grid") or {}) if cb else {}
    flag = (g.get("flag") or {}) if plant["kind"] == "value" else (g.get("mapping") or {})
    return "caught" if str(first) == str(flag.get("answer")) else "missed"


_FIRST = """SELECT DISTINCT ON (p.id) p.id::text, e.answers->'set'->>p.question
            FROM review_plant p LEFT JOIN coding_event e
              ON e.document_id = p.document_id AND e.coder_user_id = p.coder_user_id AND e.answers->>'template' = p.template
             AND e.answers->>'unit' = p.unit AND (e.answers->'set'->>p.question) IS NOT NULL
            WHERE {where} ORDER BY p.id, e.created_at"""


def with_status(conn: psycopg.Connection, where: str, args: tuple) -> list[dict]:
    """The plants a filter names, each with its ``status`` (open | caught | missed) and the first
    answer that decided it — one query for all of them."""
    plants = [_row(r) for r in conn.execute(f"SELECT {_COLS} FROM review_plant p WHERE {where} ORDER BY created_at", args).fetchall()]
    if not plants:
        return []
    first = dict(conn.execute(_FIRST.format(where=where), args).fetchall())
    for p in plants:
        p["first_answer"] = first.get(p["id"])
        p["status"] = _verdict(T.get(p["template"]), p, p["first_answer"])
    return plants


def status(conn: psycopg.Connection, plant: dict) -> str:
    return next((p["status"] for p in with_status(conn, "p.id = %s::uuid", (plant["id"],))), "open")


def summary(conn: psycopg.Connection, step_run_id: str, labels: dict[str, str]) -> list[dict]:
    """Per coder of a gate: how many plants they were shown, caught, missed, still open."""
    out: dict[str, dict] = {}
    for p in with_status(conn, "p.step_run_id = %s::uuid", (step_run_id,)):
        c = out.setdefault(p["coder_user_id"], {"user_id": p["coder_user_id"], "label": labels.get(p["coder_user_id"], "coder"),
                                                "planted": 0, "caught": 0, "missed": 0, "open": 0})
        c["planted"] += 1
        c[p["status"]] += 1
    return sorted(out.values(), key=lambda c: c["label"])


# ── into the coder's view ───────────────────────────────────────────────────────────────────

def into_view(conn: psycopg.Connection, view: dict, tpl: dict, pspec: dict, sampled: dict | None, run: dict, step_run: dict,
              document_id: str, coder_user_id: str) -> dict:
    """The coder's task view with their plant: while open, ``task.shown`` names the unit, the
    field and the value to show, and a value is written into the exhibit's transcription as well;
    once caught, ``task.caught`` says so and the view is as it truly is; once missed, nothing."""
    plant = ensure(conn, view, tpl, pspec, sampled, run, step_run, document_id, coder_user_id)
    if plant is None:
        return view
    st = status(conn, plant)
    task = view.setdefault("task", {})
    if st == "caught":
        task["caught"] = [{"unit": plant["unit"], "field": plant["field"], "kind": plant["kind"], "shown": plant["planted"]}]
        return view
    if st != "open":
        return view
    task["shown"] = [{"unit": plant["unit"], "field": plant["field"], "value": plant["planted"]}]
    cb = config(tpl)
    if plant["kind"] == "value" and cb and tpl.get("items"):
        g = cb[1].get("grid") or {}
        unit = next((u for u in T.unit_rows(cb[1], view, pspec, sampled=sampled) if u["key"] == plant["unit"]), None)
        pm = view.get("paper_metadata") if isinstance(view.get("paper_metadata"), dict) else {}
        items = pm.get(tpl["items"])
        if unit and isinstance(items, list):
            row = unit.get("row") or {}
            for k, it in enumerate(items):
                if not (isinstance(it, dict) and isinstance(it.get("text"), str) and _caption_names(it["text"], row.get(g.get("of")))):
                    continue
                spot = locate_cell(it["text"], row.get(g.get("panel")) if g.get("panel") else None, row.get(g.get("row")), row.get(g.get("column")))
                if spot:
                    items = list(items)
                    items[k] = {**it, "text": replace_cell(it["text"], spot[0], spot[1], plant["planted"])}
                    view["paper_metadata"] = {**pm, tpl["items"]: items}
                break
    return view


def _caption_names(text: str, xid: Any) -> bool:
    want = str(xid if xid is not None else "").strip().lower()
    if not want:
        return False
    first = next((ln.strip() for ln in str(text).split("\n") if ln.strip()), "")
    m = re.match(r"^\*\*(.+?)\*\*$", first)
    cap = (m.group(1) if m else first).strip().lower()
    return cap == want or any(cap.startswith(want + s) for s in (".", ":", " ", ","))


def caught_now(conn: psycopg.Connection, run_id: str, step_id: str, document_id: str, coder_user_id: str, units: set[str]) -> list[dict]:
    """After the coder's answers: their plant on this paper, when one of these units is it and it
    now stands caught — for the workspace to say so at once."""
    plants = with_status(conn, "p.run_id = %s::uuid AND p.step_id = %s AND p.document_id = %s::uuid AND p.coder_user_id = %s::uuid",
                         (run_id, step_id, document_id, coder_user_id))
    return [{"unit": p["unit"], "field": p["field"], "kind": p["kind"], "shown": p["planted"]} for p in plants if p["unit"] in units and p["status"] == "caught"]
