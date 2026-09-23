"""Harmonise a column into concepts — the taxonomy, as an optional feature of a dataset.

A text column of a dataset ("cause", "effect", an instrument name …) holds the phrases the
papers use. A vocabulary groups those phrases into CONCEPTS so that two papers talking about
the same variable meet at the same node and two different variables never do: an LLM
structure pass over the distinct values proposes the concepts (id, parent, label, definition,
direction, aliases, members, what is left out and why), the owner reviews and commits, and
the committed vocabulary resolves every value deterministically into two extra columns of the
analysis table — ``<column>_concept`` and ``<column>_polarity``. When the dataset grows, only
the values the vocabulary does not cover are proposed for (map | new | skip) and a new version
is committed. Releases freeze the vocabularies they were cut with.

Nothing here runs unless the owner opens it; a dataset without a vocabulary is untouched.
The rules and shapes follow the danish-register-econ pipeline's taxonomy protocol.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter

from psycopg.types.json import Json

from . import records

DIRECTIONS = ("higher_is_more", "higher_is_less", "not_ordered")
POLARITIES = ("higher_is_more", "higher_is_less")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,2}$")
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_COLS = ("id::text, dataset_id::text, unit, \"column\", version, status, concepts, left_out, assignments, model, prompt_sha256, proposal, "
         "owner_user_id::text, created_at, committed_at")


def _row(r) -> dict:
    return {"id": r[0], "dataset_id": r[1], "unit": r[2], "column": r[3], "version": r[4], "status": r[5], "concepts": r[6] or [],
            "left_out": r[7] or [], "assignments": r[8] or {}, "model": r[9], "prompt_sha256": r[10], "proposal": r[11] or {},
            "owner_user_id": r[12], "created_at": r[13].isoformat(timespec="seconds") if r[13] else None,
            "committed_at": r[14].isoformat(timespec="seconds") if r[14] else None}


def norm(value) -> str:
    """The comparison form of a phrase: lower case, one space, no surrounding punctuation."""
    s = re.sub(r"\s+", " ", str(value if value is not None else "")).strip().lower()
    return s.strip(" .,;:'\"()[]")


# ── the values of a column ────────────────────────────────────────────────────────────────────
def values(conn, dataset_id: str, unit: str, column: str, *, release: dict | None = None, examples: bool = True) -> list[dict]:
    """The distinct values of ``column`` in ``unit``, indexed V001…, with how often and in how
    many papers each occurs and up to two verbatim quotes behind it."""
    from . import analysis_table
    t = analysis_table.build(conn, dataset_id, unit, owner=False, release=release, crosscheck=False, vocabulary=False)
    if t is None:
        return []
    names = [c["name"] for c in t["columns"]]
    if column not in names:
        return []
    k, kp = names.index(column), names.index("_study") if "_study" in names else None
    seen: dict[str, dict] = {}
    for row in t["rows"]:
        v = row["v"][k]
        if v in (None, ""):
            continue
        key = norm(v)
        if not key:
            continue
        e = seen.setdefault(key, {"value": str(v).strip(), "spellings": [], "n": 0, "papers": set(), "cells": []})
        e["n"] += 1
        if str(v).strip() not in e["spellings"]:
            e["spellings"].append(str(v).strip())
        if kp is not None:
            e["papers"].add(row["v"][kp])
        if len(e["cells"]) < 2:
            e["cells"].append({"record_id": t["records"][row["r"]]["id"], "path": row["p"], "column": column})
    out = []
    for i, key in enumerate(sorted(seen, key=lambda s: (-seen[s]["n"], s))):
        e = seen[key]
        out.append({"idx": f"V{i + 1:03d}", "value": e["value"], "spellings": e["spellings"][:5], "n": e["n"], "n_papers": len(e["papers"]), "quotes": [], "_cells": e["cells"]})
    if examples and out:
        cells = [c for e in out for c in e["_cells"]]
        try:
            evs = analysis_table.cell_evidence(conn, dataset_id, unit, cells, release=release, max_cells=None, read_layout=False)
        except Exception:      # noqa: BLE001 — examples are a courtesy, never a blocker
            evs = []
        at = {(c["record_id"], c["path"], c["column"]): ev for c, ev in zip(cells, evs)}
        for e in out:
            for c in e["_cells"]:
                ev = at.get((c["record_id"], c["path"], c["column"])) or {}
                for it in (ev.get("items") or [])[:1]:
                    if it.get("snippet") and it["snippet"] not in e["quotes"]:
                        e["quotes"].append(it["snippet"][:300])
    for e in out:
        e.pop("_cells", None)
    return out


# ── the prompts ──────────────────────────────────────────────────────────────────────────────
def build_prompt(dataset_title: str, column_label: str, column_help: str, vals: list[dict], existing: list[dict] | None = None) -> str:
    lines = [f"# Structure pass: the concept vocabulary of the column “{column_label}” of the dataset “{dataset_title}” ({len(vals)} distinct phrases)", "",
             "## What you are building", "",
             "The nodes of a concept vocabulary. Each phrase below is how a paper names a variable" + (f" ({column_help})" if column_help else "") + ".",
             "Group the phrases into CONCEPTS so that two papers talking about the same variable meet at the same node, and two different variables never do.",
             "Every phrase is placed under exactly one concept, or left out with a reason.", "",
             "## Rules that decide placement", "",
             "1. A node is a VARIABLE, never a statistic, a population or a setting of one. \"earnings\", \"annual earnings\" and \"labor market earnings\" are one concept. A phrase that is really a group, a period or a design goes to left_out (reason: stratification / design_artifact).",
             "2. Mirror images are ONE concept. \"marginal tax rate\" and \"marginal net-of-tax rate\", \"employment\" and \"non-employment\", \"mortality\" and \"survival\" are the same variable read in opposite directions. Name the concept for ONE orientation (the one most papers use), make both phrases members, and record the mirror image in member_polarity: {\"V012\": \"higher_is_less\"}. Members not listed are higher_is_more. Getting it wrong inverts an edge.",
             "3. Kinds are sub-concepts when the papers distinguish them: labor income vs capital income under income; self-reported vs third-party-reported income; mother's vs father's death. At most three segments (domain.leaf.sub).",
             "4. A different variable is a different concept even when words overlap: a different unit of observation (firm vs worker), a between-group gap (the gender gap in X is not X), a ratio of two quantities.",
             "5. Exposures are variables too. A policy, event or treatment (\"tax audit\", \"parental death\", \"import competition\") is a concept whose more_means is \"exposed / more exposure\".",
             "6. Direction is relative to the parent and every concept declares one: higher_is_more, higher_is_less (more of this is LESS of the parent — health.mortality under health), or not_ordered. A top-level concept (domain.leaf) is never higher_is_less: name it for what MORE of it is. Fill more_means.",
             "7. Ids are domain.leaf or domain.leaf.sub: lowercase, digits, _. parent is the id minus its last segment (a domain, or a domain.leaf concept you also define).",
             "8. Aliases are other spellings a resolver should catch next time — unique across concepts; do not repeat a member phrase as an alias.",
             "9. Reference phrases ONLY by index (V001…). Every index exactly once, in one concept's member_values or in left_out.", ""]
    if existing:
        lines += ["## Concepts that already exist (keep their ids; you may add members and aliases to them, never redefine them)", ""]
        lines += [f"- {c['id']} · {c.get('label')} · {c.get('direction')} · {c.get('definition') or ''}" for c in existing] + [""]
    lines += ["## The phrases", ""]
    for v in vals:
        sp = f" (also written: {', '.join(v['spellings'][1:])})" if len(v.get("spellings") or []) > 1 else ""
        q = "".join(f"\n    “{x}”" for x in (v.get("quotes") or [])[:2])
        lines.append(f"{v['idx']}  \"{v['value']}\"{sp} — {v['n']} row{'s' if v['n'] != 1 else ''}, {v['n_papers']} paper{'s' if v['n_papers'] != 1 else ''}{q}")
    lines += ["", "## Return ONLY this JSON object", "",
              '{"domains": [{"id": "income", "label": "Income"}],',
              ' "concepts": [{"id": "income.labor_income", "parent": "income", "label": "Labor income", "definition": "one sentence: what the variable is",',
              '               "direction": "higher_is_more", "more_means": "more money earned from work", "aliases": ["wage income"],',
              '               "member_values": ["V007", "V027"], "member_polarity": {"V027": "higher_is_less"}, "rationale": "why these are one variable"}],',
              ' "left_out": [{"value": "V009", "reason": "stratification", "why": "one sentence"}]}', ""]
    return "\n".join(lines)


def residual_prompt(dataset_title: str, column_label: str, vocab: dict, vals: list[dict]) -> str:
    lines = [f"You are extending the concept vocabulary of the column “{column_label}” of the dataset “{dataset_title}”, bottom-up from what papers actually measure.",
             "The vocabulary was built from the papers' own phrases and reviewed by a person; your job is to place the phrases it does not yet cover.", "",
             "CURRENT VOCABULARY (id · label · direction · definition · aliases):"]
    for c in vocab.get("concepts") or []:
        lines.append(f"- {c['id']} · {c.get('label')} · {c.get('direction')} · {c.get('definition') or ''} · aliases: {', '.join(c.get('aliases') or []) or '—'}")
    lines += ["", "PHRASES TO PLACE:"]
    for v in vals:
        q = "".join(f"\n    “{x}”" for x in (v.get("quotes") or [])[:2])
        lines.append(f"{v['idx']}  \"{v['value']}\" — {v['n']} row{'s' if v['n'] != 1 else ''}, {v['n_papers']} paper{'s' if v['n_papers'] != 1 else ''}{q}")
    lines += ["", "For each phrase decide exactly one of:",
              "  - \"map\":  an existing concept genuinely covers it → give its concept_id and, when the phrase is its mirror image, polarity higher_is_less",
              "  - \"new\":  a distinct variable → concept_id as domain.leaf or domain.leaf.sub (reuse a domain where one fits), parent (the id minus its last segment), direction relative to the parent (higher_is_more | higher_is_less | not_ordered), label_text, a one-sentence definition, aliases",
              "  - \"skip\": not a measurable construct (a running variable, an instrument, a control-list dump, a sample split, a whole sentence) → reason", "",
              "Rules that matter: a node is a VARIABLE, never a statistic of one — the level, growth, log, a threshold indicator or a quantile of one quantity map to ONE concept; prefer map over new. Never map two different variables together (a different unit of observation, a between-group contrast, a ratio). A qualifier on the event can be a sub-concept; a qualifier on WHO is in the sample is a skip. A top-level concept is never higher_is_less. Never rename or remove an existing id. Reference phrases ONLY by index. One sentence of why per decision.", "",
              "Return ONLY JSON:", '{"decisions": [{"value": "V001", "action": "map|new|skip", "concept_id": "domain.leaf or null", "parent": "... or null", "direction": "... or null",',
              '                "polarity": "higher_is_more|higher_is_less|null", "label_text": "... or null", "definition": "... or null", "aliases": ["..."], "why": "...", "reason": "... or null"}]}', ""]
    return "\n".join(lines)


# ── validation and resolution (pure) ─────────────────────────────────────────────────────────
def validate_draft(draft: dict, vals: list[dict], existing: list[dict] | None = None) -> tuple[dict, list[str]]:
    """The model's (or the reviewer's) draft made consistent: ids well-formed, parents present,
    every index placed exactly once, directions legal. Returns (clean draft, repairs made)."""
    repairs: list[str] = []
    idx = {v["idx"] for v in vals}
    concepts: list[dict] = []
    seen_ids: set[str] = set()
    for c in (draft.get("concepts") or []) if isinstance(draft, dict) else []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip().lower()
        cid = re.sub(r"[^a-z0-9_.]", "_", cid)
        if not _ID_RE.match(cid):
            repairs.append(f"dropped concept with malformed id {c.get('id')!r}"); continue
        if cid in seen_ids:
            repairs.append(f"dropped duplicate concept {cid}"); continue
        seen_ids.add(cid)
        parent = cid.rsplit(".", 1)[0]
        direction = c.get("direction") if c.get("direction") in DIRECTIONS else "higher_is_more"
        if c.get("direction") not in DIRECTIONS:
            repairs.append(f"{cid}: direction {c.get('direction')!r} → higher_is_more")
        if cid.count(".") == 1 and direction == "higher_is_less":
            direction = "higher_is_more"; repairs.append(f"{cid}: a top-level concept is never higher_is_less → higher_is_more")
        members = [m for m in (c.get("member_values") or []) if isinstance(m, str) and m in idx]
        pol = {m: p for m, p in (c.get("member_polarity") or {}).items() if isinstance(c.get("member_polarity"), dict) and m in members and p in POLARITIES}
        aliases = []
        for a in c.get("aliases") or []:
            if isinstance(a, str) and norm(a) and norm(a) not in {norm(x) for x in aliases}:
                aliases.append(a.strip())
        concepts.append({"id": cid, "parent": parent, "label": (str(c.get("label") or cid.rsplit(".", 1)[-1].replace("_", " "))).strip()[:120],
                         "definition": (str(c.get("definition") or "")).strip()[:600], "direction": direction, "more_means": (str(c.get("more_means") or "")).strip()[:200],
                         "aliases": aliases[:30], "member_values": members, "member_polarity": pol, "rationale": (str(c.get("rationale") or "")).strip()[:600]})
    # parents: a domain.leaf.sub needs its domain.leaf; make it when missing
    ids = {c["id"] for c in concepts}
    for c in list(concepts):
        if c["id"].count(".") == 2 and c["parent"] not in ids:
            concepts.append({"id": c["parent"], "parent": c["parent"].rsplit(".", 1)[0], "label": c["parent"].rsplit(".", 1)[-1].replace("_", " "), "definition": "",
                             "direction": "higher_is_more", "more_means": "", "aliases": [], "member_values": [], "member_polarity": {}, "rationale": "added as the parent of " + c["id"]})
            ids.add(c["parent"]); repairs.append(f"added missing parent {c['parent']} for {c['id']}")
    # every index exactly once
    placed: Counter = Counter(m for c in concepts for m in c["member_values"])
    for c in concepts:
        kept = []
        for m in c["member_values"]:
            if placed[m] > 1:
                placed[m] -= 1; repairs.append(f"{m} was placed in several concepts; kept the last")
                continue
            kept.append(m)
        c["member_values"] = kept
        c["member_polarity"] = {m: p for m, p in c["member_polarity"].items() if m in kept}
    placed_set = {m for c in concepts for m in c["member_values"]}
    left_out = []
    seen_lo: set[str] = set()
    for lo in (draft.get("left_out") or []) if isinstance(draft, dict) else []:
        if isinstance(lo, dict) and lo.get("value") in idx and lo["value"] not in placed_set and lo["value"] not in seen_lo:
            seen_lo.add(lo["value"])
            left_out.append({"value": lo["value"], "reason": (str(lo.get("reason") or "unplaced")).strip()[:60], "why": (str(lo.get("why") or "")).strip()[:300]})
    for v in vals:
        if v["idx"] not in placed_set and v["idx"] not in seen_lo:
            left_out.append({"value": v["idx"], "reason": "unplaced", "why": "the model did not place this phrase"}); repairs.append(f"{v['idx']} was not placed: left out")
    domains = []
    for d in (draft.get("domains") or []) if isinstance(draft, dict) else []:
        if isinstance(d, dict) and _DOMAIN_RE.match(str(d.get("id") or "")):
            domains.append({"id": d["id"], "label": (str(d.get("label") or d["id"])).strip()[:80]})
    for dom in sorted({c["id"].split(".")[0] for c in concepts}):
        if dom not in {d["id"] for d in domains}:
            domains.append({"id": dom, "label": dom.replace("_", " ")})
    concepts.sort(key=lambda c: c["id"])
    return {"domains": domains, "concepts": concepts, "left_out": left_out}, repairs


def resolve(vals: list[dict], vocab: dict, prior: dict | None = None) -> dict:
    """{normalised value: {value, concept_id, polarity, basis}} for every value: a member of a
    concept (basis "member"), an alias or label match ("alias"), kept from an earlier version
    ("prior"), or unresolved."""
    by_idx = {v["idx"]: v for v in vals}
    out: dict[str, dict] = {}
    for c in vocab.get("concepts") or []:
        for m in c.get("member_values") or []:
            v = by_idx.get(m)
            if v:
                out[norm(v["value"])] = {"value": v["value"], "concept_id": c["id"], "polarity": (c.get("member_polarity") or {}).get(m, "higher_is_more"), "basis": "member"}
    alias_of = {}
    for c in vocab.get("concepts") or []:
        for a in [c.get("label")] + list(c.get("aliases") or []):
            if a and norm(a):
                alias_of.setdefault(norm(a), c["id"])
    for lo in vocab.get("left_out") or []:
        v = by_idx.get(lo.get("value"))
        if v and norm(v["value"]) not in out:
            out[norm(v["value"])] = {"value": v["value"], "concept_id": None, "polarity": None, "basis": "left_out", "reason": lo.get("reason")}
    for v in vals:
        key = norm(v["value"])
        if key in out:
            continue
        if key in alias_of:
            out[key] = {"value": v["value"], "concept_id": alias_of[key], "polarity": "higher_is_more", "basis": "alias"}
        elif prior and key in prior and prior[key].get("concept_id"):
            out[key] = {**prior[key], "basis": "prior"}
        else:
            out[key] = {"value": v["value"], "concept_id": None, "polarity": None, "basis": "unresolved"}
    return out


def unresolved(vals: list[dict], assignments: dict) -> list[dict]:
    """The values of the live column the committed vocabulary does not cover."""
    return [v for v in vals if (assignments.get(norm(v["value"])) or {}).get("basis") in (None, "unresolved")]


def apply_decisions(vocab: dict, vals: list[dict], decisions: list[dict]) -> tuple[dict, list[str]]:
    """The reviewed residual decisions (map | new | skip) folded into a vocabulary → the next
    version's draft (validated again afterwards)."""
    by_idx = {v["idx"]: v for v in vals}
    concepts = {c["id"]: {**c, "member_values": list(c.get("member_values") or []), "member_polarity": dict(c.get("member_polarity") or {}), "aliases": list(c.get("aliases") or [])}
                for c in vocab.get("concepts") or []}
    left_out = [dict(x) for x in vocab.get("left_out") or []]
    notes: list[str] = []
    for d in decisions:
        v = by_idx.get(d.get("value"))
        if not v:
            continue
        act = d.get("action")
        if act == "map" and d.get("concept_id") in concepts:
            c = concepts[d["concept_id"]]
            if v["idx"] not in c["member_values"]:
                c["member_values"].append(v["idx"])
            if d.get("polarity") == "higher_is_less":
                c["member_polarity"][v["idx"]] = "higher_is_less"
        elif act == "new" and d.get("concept_id"):
            cid = str(d["concept_id"]).strip().lower()
            c = concepts.setdefault(cid, {"id": cid, "parent": cid.rsplit(".", 1)[0], "label": d.get("label_text") or cid.rsplit(".", 1)[-1].replace("_", " "),
                                          "definition": d.get("definition") or "", "direction": d.get("direction") or "higher_is_more", "more_means": "",
                                          "aliases": list(d.get("aliases") or []), "member_values": [], "member_polarity": {}, "rationale": d.get("why") or ""})
            if v["idx"] not in c["member_values"]:
                c["member_values"].append(v["idx"])
        elif act == "skip":
            left_out.append({"value": v["idx"], "reason": (d.get("reason") or "skipped")[:60], "why": (d.get("why") or "")[:300]})
        else:
            notes.append(f"{v['idx']}: decision {act!r} could not be applied")
    return {"domains": vocab.get("domains") or [], "concepts": list(concepts.values()), "left_out": left_out}, notes


# ── storage ──────────────────────────────────────────────────────────────────────────────────
def list_for_dataset(conn, dataset_id: str) -> list[dict]:
    return [_row(r) for r in conn.execute(f"SELECT {_COLS} FROM dataset_vocabulary WHERE dataset_id = %s::uuid ORDER BY unit, \"column\", version DESC",
                                           (dataset_id,)).fetchall()]


def get(conn, vid: str) -> dict | None:
    if not records._is_uuid(vid):   # noqa: SLF001
        return None
    r = conn.execute(f"SELECT {_COLS} FROM dataset_vocabulary WHERE id = %s::uuid", (vid,)).fetchone()
    return _row(r) if r else None


def committed(conn, dataset_id: str) -> list[dict]:
    """The latest committed vocabulary per (unit, column)."""
    out: dict[tuple, dict] = {}
    for v in list_for_dataset(conn, dataset_id):
        if v["status"] == "committed" and (v["unit"], v["column"]) not in out:
            out[(v["unit"], v["column"])] = v
    return list(out.values())


def create(conn, dataset_id: str, *, unit: str, column: str, draft: dict, model: str | None, prompt_sha256: str | None, proposal: dict | None,
           owner_user_id: str | None, status: str = "draft", assignments: dict | None = None) -> dict:
    vid = str(uuid.uuid4())
    with conn.transaction():
        n = conn.execute("SELECT coalesce(max(version), 0) + 1 FROM dataset_vocabulary WHERE dataset_id = %s::uuid AND unit = %s AND \"column\" = %s",
                         (dataset_id, unit, column)).fetchone()[0]
        conn.execute("""INSERT INTO dataset_vocabulary (id, dataset_id, unit, "column", version, status, concepts, left_out, assignments, model, prompt_sha256, proposal, owner_user_id, committed_at)
                        VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, CASE WHEN %s = 'committed' THEN now() ELSE NULL END)""",
                     (vid, dataset_id, unit, column, n, status, Json(draft.get("concepts") or []), Json(draft.get("left_out") or []), Json(assignments or {}),
                      model, prompt_sha256, Json(proposal or {}), owner_user_id, status))
        conn.execute("UPDATE dataset_vocabulary SET proposal = proposal || %s::jsonb WHERE id = %s::uuid", (Json({"domains": draft.get("domains") or []}), vid))
    return get(conn, vid)


def update_draft(conn, vid: str, draft: dict) -> dict | None:
    with conn.transaction():
        conn.execute("UPDATE dataset_vocabulary SET concepts = %s, left_out = %s, proposal = proposal || %s::jsonb WHERE id = %s::uuid AND status = 'draft'",
                     (Json(draft.get("concepts") or []), Json(draft.get("left_out") or []), Json({"domains": draft.get("domains") or []}), vid))
    return get(conn, vid)


def commit(conn, vid: str, assignments: dict) -> dict | None:
    with conn.transaction():
        conn.execute("UPDATE dataset_vocabulary SET status = 'committed', assignments = %s, committed_at = now() WHERE id = %s::uuid", (Json(assignments), vid))
    return get(conn, vid)


def delete(conn, vid: str) -> int:
    with conn.transaction():
        return conn.execute("DELETE FROM dataset_vocabulary WHERE id = %s::uuid AND status = 'draft'", (vid,)).rowcount


def as_draft(v: dict) -> dict:
    return {"domains": (v.get("proposal") or {}).get("domains") or [], "concepts": v.get("concepts") or [], "left_out": v.get("left_out") or []}


def prompt_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── what the analysis table and the releases read ────────────────────────────────────────────
def snapshot(conn, dataset_id: str) -> list[dict]:
    """The committed vocabularies, frozen into a release: what resolves the columns there."""
    return [{"id": v["id"], "unit": v["unit"], "column": v["column"], "version": v["version"], "committed_at": v["committed_at"],
             "domains": (v.get("proposal") or {}).get("domains") or [], "concepts": v["concepts"], "left_out": v["left_out"], "assignments": v["assignments"]}
            for v in committed(conn, dataset_id)]


def extra_columns(conn, dataset_id: str, column_names: list[str], release: dict | None = None) -> list[tuple[dict, dict]]:
    """[(column definition, {normalised value: (concept_id, polarity)})] for every committed
    vocabulary whose column is in this unit — two columns each: <column>_concept, <column>_polarity."""
    vocabs = (release.get("snapshot") or {}).get("vocabularies") if release is not None else None
    if vocabs is None:
        vocabs = snapshot(conn, dataset_id) if release is None else []
    out = []
    for v in vocabs:
        col = v.get("column")
        if col not in column_names:
            continue
        labels = {c["id"]: c.get("label") or c["id"] for c in v.get("concepts") or []}
        mapping = {k: (a.get("concept_id"), a.get("polarity")) for k, a in (v.get("assignments") or {}).items() if a.get("concept_id")}
        out.append(({"name": f"{col}_concept", "label": f"{col.replace('_', ' ')} · concept", "type": "string", "scope": "vocabulary",
                     "help": f"the concept the {col.replace('_', ' ')} phrase resolves to in vocabulary v{v.get('version')}", "options": None,
                     "roles": ["dimension"], "vocabulary": {"version": v.get("version"), "labels": labels}},
                    {"name": f"{col}_polarity", "label": f"{col.replace('_', ' ')} · polarity", "type": "enum", "scope": "vocabulary",
                     "help": "higher_is_less when the phrase is the mirror image of its concept", "options": list(POLARITIES), "roles": ["dimension"]},
                    col, mapping))
    return out
