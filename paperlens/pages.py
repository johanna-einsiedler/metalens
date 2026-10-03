"""A dataset's living pages — a design's or a theory's "wiki" entry, as reviewable data.

A page belongs to a GROUP (design, theory, overview, …) and a KEY (a concept id, an enum
value, or ``_index``), and is written by one model call from the dataset's own rows: the
claims whose keyed column matches, and the papers' extracted summaries. Its prose carries
an audit trail: every section lists the records it was written from, and a section with no
source from the provided set is rejected the way an unplaced vocabulary value is repaired.
``curated_md`` is the human-written lede; no model call ever writes it.

Updates are reviewed as CHANGES, never as a fresh page: a draft v(N+1) is compared with the
committed vN — per-section verdicts (kept | revised | new | removed) computed by string
diff, and the sources behind a change split into *new since vN* (the evidence that
triggered it, computed from record membership, never from the model's say-so) and *carried*.
The model's own ``change_log`` is validated against that computed set and mismatches are
reported as repairs. ``dashboard_diff`` is the in-house precedent: verdicts before an
update is accepted, computed, not narrated.

Releases freeze whichever version was committed at cut time (``snapshot``), and the export
writes them to ``pages.json`` — pages are dataset content, not decoration.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import uuid

from psycopg.types.json import Json

from . import records

GROUPS_HELP = "a short lowercase word: design | theory | overview | …"
_COLS = ('id::text, dataset_id::text, "group", key, version, status, title, curated_md, sections, change_log, '
         "model, prompt_sha256, built_from, proposal, owner_user_id::text, created_at, committed_at")


def _row(r) -> dict:
    return {"id": r[0], "dataset_id": r[1], "group": r[2], "key": r[3], "version": r[4], "status": r[5],
            "title": r[6], "curated_md": r[7], "sections": r[8] or [], "change_log": r[9] or [],
            "model": r[10], "prompt_sha256": r[11], "built_from": r[12] or {}, "proposal": r[13] or {},
            "owner_user_id": r[14],
            "created_at": r[15].isoformat(timespec="seconds") if r[15] else None,
            "committed_at": r[16].isoformat(timespec="seconds") if r[16] else None}


# ── what a page is about ────────────────────────────────────────────────────────────────

def rows_for(conn, dataset_id: str, keyed_by: str, key: str, *, release: dict | None = None) -> list[dict]:
    """The entry rows whose ``keyed_by`` column matches ``key`` — the page's subject matter.

    ``keyed_by`` is a column of the ENTRIES unit: an enum/string (``theory_model``), a multi
    (``design`` — match = membership), or a vocabulary concept column
    (``identifying_variation_concept``). Each row comes back as {record_id, study, title,
    fields: {column: value}} with every entry column, so the prompt builder and the UI pick
    what they need.
    """
    from . import analysis_table
    t = analysis_table.build(conn, dataset_id, "entries", owner=True, release=release)
    if t is None:
        return []
    cols = [c["name"] for c in t["columns"]]
    if keyed_by not in cols:
        return _rows_for_paper_key(conn, dataset_id, keyed_by, key, t, cols)
    ki = cols.index(keyed_by)
    recs = t.get("records") or []
    out = []
    for row in t["rows"]:
        v = row["v"] if isinstance(row, dict) else row
        cell = v[ki]
        if isinstance(cell, list):
            hit = key in cell
        elif isinstance(cell, str) and "; " in cell:    # a multi the table flattened to "a; b"
            hit = key in cell.split("; ")
        else:
            hit = cell == key
        if not hit:
            continue
        ri = row.get("r") if isinstance(row, dict) else None
        rec = recs[ri] if isinstance(ri, int) and 0 <= ri < len(recs) else {}
        out.append({"record_id": rec.get("id"), "fields": dict(zip(cols, v))})
    return out


def _rows_for_paper_key(conn, dataset_id: str, keyed_by: str, key: str, t: dict, cols: list) -> list[dict]:
    """A page keyed by a PAPER-level field (theory pages): every entry row of the documents
    whose field matches — directly, or through the committed paper vocabulary (so the key may
    be a concept id and papers with different spellings of one model still meet)."""
    from . import vocabulary
    docs = conn.execute(
        """SELECT DISTINCT d.id::text, d.paper_metadata_raw FROM extraction_document d
           JOIN record r ON r.document_id = d.id WHERE r.dataset_id = %s::uuid""",
        (dataset_id,)).fetchall()
    asg = {}
    for v in vocabulary.committed(conn, dataset_id):
        if v.get("unit") == "paper" and v.get("column") == keyed_by:
            asg = v.get("assignments") or {}
    hit_docs = set()
    for did, pm in docs:
        val = (pm or {}).get(keyed_by)
        if val in (None, ""):
            continue
        concept = (asg.get(vocabulary.norm(val)) or {}).get("concept_id")
        if val == key or vocabulary.norm(val) == vocabulary.norm(key) or concept == key:
            hit_docs.add(did)
    if not hit_docs:
        return []
    doc_of = {r[0]: r[1] for r in conn.execute(
        """SELECT id::text, document_id::text FROM record WHERE dataset_id = %s::uuid""",
        (dataset_id,)).fetchall()}
    recs = t.get("records") or []
    out = []
    for row in t["rows"]:
        ri = row.get("r") if isinstance(row, dict) else None
        rec = recs[ri] if isinstance(ri, int) and 0 <= ri < len(recs) else {}
        rid = rec.get("id")
        if rid and doc_of.get(rid) in hit_docs:
            out.append({"record_id": rid, "fields": dict(zip(cols, row["v"] if isinstance(row, dict) else row))})
    return out


def paper_reads(conn, dataset_id: str) -> list[dict]:
    """Each paper's extracted paper-level read (the three summaries + theory codes)."""
    rows = conn.execute(
        """SELECT DISTINCT d.id::text, d.filename, d.paper_metadata_raw
           FROM extraction_document d JOIN record r ON r.document_id = d.id
           WHERE r.dataset_id = %s::uuid""", (dataset_id,)).fetchall()
    out = []
    for did, fn, pm in rows:
        pm = pm or {}
        out.append({"document_id": did, "filename": fn,
                    **{k: pm.get(k) for k in ("title", "summary", "design_summary",
                                              "theory_summary", "theory_role", "theory_model")}})
    return sorted(out, key=lambda p: p["filename"] or "")


def current_record_ids(conn, dataset_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        """SELECT id::text FROM record WHERE dataset_id = %s::uuid AND NOT screened_empty
           ORDER BY id""", (dataset_id,)).fetchall()]


def built_from(conn, dataset_id: str) -> dict:
    ids = current_record_ids(conn, dataset_id)
    return {"n_records": len(ids), "record_ids": ids,
            "record_ids_sha": hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]}


def new_since(conn, dataset_id: str, old_built_from: dict | None) -> set[str]:
    """Records added since a page's basis — the only legitimate triggers of a change.
    Computed from membership, never from what a model claims."""
    now = set(current_record_ids(conn, dataset_id))
    old = set((old_built_from or {}).get("record_ids") or [])
    return now - old if old else now


# ── the one model call ──────────────────────────────────────────────────────────────────

def build_prompt(dataset_title: str, group: str, key: str, *, concept: dict | None,
                 old_page: dict | None, curated_md: str | None, rows: list[dict],
                 reads: list[dict], new_ids: set[str]) -> str:
    head = [f"# {'Update' if old_page else 'Write'} the {group} page “{key}” of the dataset “{dataset_title}”", "",
            "You are maintaining one page of a research wiki. Its readers never see the model:",
            "they see prose WITH ITS SOURCES, so the one inviolable rule is:",
            "**every section must list the record ids it was written from** — ids from the",
            "rows below and nothing else. A sentence no row supports, you do not write.",
            "Encyclopedic tone; no evaluation, no 'importantly'; 2–6 short sections, each",
            "with a heading-free paragraph (the site renders them as paragraphs).", ""]
    if concept:
        head += ["The page's subject, as the committed vocabulary defines it:",
                 f"- `{concept.get('id')}` — {concept.get('label')}: {concept.get('definition') or '(no definition)'}", ""]
    if curated_md:
        head += ["A human wrote this lede; it renders ABOVE your sections. Do not repeat it:",
                 "", "> " + curated_md.replace("\n", "\n> "), ""]
    if old_page:
        head += ["## The committed page you are updating", "",
                 json.dumps({"sections": old_page.get("sections")}, indent=1, ensure_ascii=False), "",
                 "Change it only where the NEW rows below warrant it. Sections the new rows do",
                 "not touch come back verbatim (op `keep`). For every `revise`/`add`/`drop`,",
                 "say which NEW record ids caused it.", ""]
    head += ["## The rows (the page's only admissible sources)", ""]
    for r in rows:
        f = r["fields"]
        flat = {k: v for k, v in f.items() if v not in (None, "", []) and not k.startswith("_")}
        tag = " [NEW]" if r.get("record_id") in new_ids else ""
        head.append(f"- record {r.get('record_id')}{tag} · {f.get('_study')} · {f.get('_entry')}:")
        head.append("  " + json.dumps(flat, ensure_ascii=False)[:1200])
    head += ["", "## The papers' own extracted reads (context; cite the record ids above, not these)", ""]
    for p in reads:
        head.append(f"- {p.get('filename')}: {json.dumps({k: p.get(k) for k in ('summary', 'design_summary', 'theory_summary', 'theory_role', 'theory_model') if p.get(k)}, ensure_ascii=False)[:900]}")
    head += ["", "## Return ONLY this JSON object", "",
             '{"title": "a 2-5 word page title",',
             ' "sections": [{"text": "one paragraph", "sources": ["<record id>", "…"]}],',
             ' "change_log": [{"op": "keep|revise|add|drop", "old_section": 0, "because": ["<NEW record id>"]}]}',
             "", "`old_section` is the index into the committed page's sections (null for `add`).",
             "With no committed page, change_log is one `add` per section.", ""]
    return "\n".join(head)


def validate_draft(draft: dict, allowed_ids: set[str], new_ids: set[str],
                   old_sections: list | None) -> tuple[dict, list[str]]:
    """The model's page made honest: sections keep only sources that exist; a section with
    none left is DROPPED (prose without provenance is exactly what this feature forbids);
    change ops citing non-new 'causes' are reported."""
    repairs: list[str] = []
    sections = []
    for i, s in enumerate((draft.get("sections") or []) if isinstance(draft, dict) else []):
        if not isinstance(s, dict) or not str(s.get("text") or "").strip():
            continue
        src = [x for x in (s.get("sources") or []) if isinstance(x, str) and x in allowed_ids]
        bad = [x for x in (s.get("sources") or []) if x not in allowed_ids]
        if bad:
            repairs.append(f"section {i}: dropped unknown sources {bad[:3]}")
        if not src:
            repairs.append(f"section {i} dropped: no admissible source ({str(s.get('text'))[:60]!r})")
            continue
        sections.append({"text": str(s["text"]).strip()[:4000], "sources": sorted(set(src))})
    log = []
    n_old = len(old_sections or [])
    for e in (draft.get("change_log") or []) if isinstance(draft, dict) else []:
        if not isinstance(e, dict) or e.get("op") not in ("keep", "revise", "add", "drop"):
            continue
        because = [x for x in (e.get("because") or []) if isinstance(x, str) and x in allowed_ids]
        if e["op"] in ("revise", "add", "drop") and new_ids and not (set(because) & new_ids):
            repairs.append(f"change_log {e['op']}: cites no NEW record — flagged")
        old_i = e.get("old_section")
        log.append({"op": e["op"], "old_section": old_i if isinstance(old_i, int) and 0 <= old_i < n_old else None,
                    "because": because})
    title = (str(draft.get("title") or "") if isinstance(draft, dict) else "").strip()[:120]
    return {"title": title, "sections": sections, "change_log": log}, repairs


# ── the computed review: what changed, and which evidence moved it ───────────────────────

def section_diff(old_sections: list, new_sections: list, new_ids: set[str]) -> list[dict]:
    """Per NEW section: a verdict against the old page — computed by similarity, never taken
    from the model — plus the inline word diff and the trigger split of its sources."""
    old_texts = [s.get("text") or "" for s in old_sections or []]
    used: set[int] = set()
    out = []
    for s in new_sections or []:
        text = s.get("text") or ""
        best, best_i = 0.0, None
        for i, ot in enumerate(old_texts):
            if i in used:
                continue
            r = difflib.SequenceMatcher(None, ot, text).ratio()
            if r > best:
                best, best_i = r, i
        if best >= 0.98:
            verdict, old_i = "kept", best_i
        elif best >= 0.55:
            verdict, old_i = "revised", best_i
        else:
            verdict, old_i = "new", None
        if old_i is not None:
            used.add(old_i)
        words_old = (old_texts[old_i] if old_i is not None else "").split()
        diff: list[dict] = []
        if verdict == "revised":
            new_words = text.split()
            for op, a1, a2, b1, b2 in difflib.SequenceMatcher(None, words_old, new_words).get_opcodes():
                if op in ("equal", "insert"):
                    diff.append({"op": op, "text": " ".join(new_words[b1:b2])})
                elif op == "delete":
                    diff.append({"op": "delete", "text": " ".join(words_old[a1:a2])})
                else:                               # replace = a delete beside an insert
                    diff.append({"op": "delete", "text": " ".join(words_old[a1:a2])})
                    diff.append({"op": "insert", "text": " ".join(new_words[b1:b2])})
        srcs = s.get("sources") or []
        out.append({"verdict": verdict, "old_section": old_i, "text": text,
                    "diff": diff,
                    "sources_new": sorted(x for x in srcs if x in new_ids),
                    "sources_carried": sorted(x for x in srcs if x not in new_ids),
                    "unexplained": verdict in ("revised", "new") and bool(new_ids)
                                   and not any(x in new_ids for x in srcs)})
    for i, ot in enumerate(old_texts):
        if i not in used:
            out.append({"verdict": "removed", "old_section": i, "text": ot, "diff": [],
                        "sources_new": [], "sources_carried": [], "unexplained": False})
    return out


# ── storage ──────────────────────────────────────────────────────────────────────────────

def list_for_dataset(conn, dataset_id: str) -> list[dict]:
    return [_row(r) for r in conn.execute(
        f'SELECT {_COLS} FROM dataset_page WHERE dataset_id = %s::uuid ORDER BY "group", key, version',
        (dataset_id,)).fetchall()]


def get(conn, pid: str) -> dict | None:
    if not records._is_uuid(pid):   # noqa: SLF001
        return None
    r = conn.execute(f"SELECT {_COLS} FROM dataset_page WHERE id = %s::uuid", (pid,)).fetchone()
    return _row(r) if r else None


def committed(conn, dataset_id: str, group: str, key: str) -> dict | None:
    r = conn.execute(
        f"""SELECT {_COLS} FROM dataset_page WHERE dataset_id = %s::uuid AND "group" = %s AND key = %s
            AND status = 'committed' ORDER BY version DESC LIMIT 1""",
        (dataset_id, group, key)).fetchone()
    return _row(r) if r else None


def create(conn, dataset_id: str, *, group: str, key: str, draft: dict, curated_md: str | None,
           model: str | None, prompt_sha256: str | None, basis: dict, proposal: dict | None,
           owner_user_id: str | None) -> dict:
    pid = str(uuid.uuid4())
    group = re.sub(r"[^a-z0-9_]", "", (group or "").lower())[:40] or "page"
    with conn.transaction():
        n = conn.execute(
            'SELECT coalesce(max(version), 0) FROM dataset_page WHERE dataset_id = %s::uuid AND "group" = %s AND key = %s',
            (dataset_id, group, key)).fetchone()[0]
        conn.execute(
            """INSERT INTO dataset_page (id, dataset_id, "group", key, version, status, title, curated_md,
                                         sections, change_log, model, prompt_sha256, built_from, proposal, owner_user_id)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid)""",
            (pid, dataset_id, group, key, n + 1, draft.get("title") or key, curated_md,
             Json(draft.get("sections") or []), Json(draft.get("change_log") or []),
             model, prompt_sha256, Json(basis), Json(proposal or {}), owner_user_id))
    return get(conn, pid)


def update_draft(conn, pid: str, *, title: str | None, sections: list, curated_md: str | None) -> dict | None:
    with conn.transaction():
        n = conn.execute(
            """UPDATE dataset_page SET title = coalesce(%s, title), sections = %s,
                      curated_md = coalesce(%s, curated_md)
               WHERE id = %s::uuid AND status = 'draft'""",
            (title, Json(sections), curated_md, pid)).rowcount
    return get(conn, pid) if n else None


def commit(conn, pid: str) -> dict | None:
    with conn.transaction():
        n = conn.execute(
            "UPDATE dataset_page SET status = 'committed', committed_at = now() WHERE id = %s::uuid AND status = 'draft'",
            (pid,)).rowcount
    return get(conn, pid) if n else None


def delete(conn, pid: str) -> int:
    with conn.transaction():
        return conn.execute("DELETE FROM dataset_page WHERE id = %s::uuid AND status = 'draft'", (pid,)).rowcount


def snapshot(conn, dataset_id: str) -> list[dict]:
    """The committed pages, newest version per (group, key) — what a release freezes."""
    rows = [r for r in list_for_dataset(conn, dataset_id) if r["status"] == "committed"]
    best: dict[tuple, dict] = {}
    for r in rows:
        k = (r["group"], r["key"])
        if k not in best or r["version"] > best[k]["version"]:
            best[k] = r
    return [{k: v for k, v in r.items() if k in ("group", "key", "version", "title", "curated_md",
                                                 "sections", "model", "committed_at")}
            for r in sorted(best.values(), key=lambda x: (x["group"], x["key"]))]


def prompt_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
