"""Publish the four MASEMiner evaluation datasets: the unverified gpt-5.6-sol output of each use
case, imported with its PDFs, then corrected cell by cell to the reference database of the
meta-analysis ("ground truth") — every deviation the evaluation counted (omission, over-extraction,
wrong value) becomes a human correction on the record, and every record ends verified. The
published values therefore equal the reference database; the audit trail shows what the model
got wrong.

    .venv/bin/python scripts/publish_masem_evaluation.py --data "<…/03_data>" --results "<…/05_results>" \
        --base https://metalens.tech --email you@example.org [--only self-other,video,tas20,ncs18] [--dry-run]

``--dry-run`` touches no server: it converts every paper, finds every PDF and plans every correction
against the exported entries, reporting anything the cells file names that cannot be located.
Inputs: ``03_data/codings_MASEMiner/<case>/`` (the MASEMiner exports of the manuscript's run),
``03_data/**/<filename>.pdf`` (the PDFs the model read, ``_AE`` variants included), and
``05_results/MASEMiner_vs_ground_truth_cells.csv`` (condition "without correction"). The password
is asked for (or read from PAPERLENS_PASSWORD); the account becomes the owner of the datasets. Re-runs skip papers already in the
dataset. Publishing to GitHub / the catalogue / Zenodo stays a decision on the dataset page.
"""
from __future__ import annotations

import argparse
import copy
import csv
import getpass
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import httpx

CASES = {
    "self-other": dict(
        sub="self-other_gnambs2013", case="Self-other correlations", preset="masem-direct", kind="direct",
        title="MASEMiner evaluation · Self–other correlations of the Big Five (Gnambs, 2013)",
        description="Ten primary studies of the meta-analysis by Gnambs (2013) on self- and other-rated Big Five "
                    "traits, extracted with the MASEMiner direct-information preset (gpt-5.6-sol) and then corrected "
                    "cell by cell to the reference database of the meta-analysis. Each correction is a recorded human "
                    "edit, so the published values equal the reference coding and the audit trail shows the model's "
                    "omissions, over-extractions and wrong values. Prompt-development case of Schroeders, Gnambs & "
                    "Einsiedler (2026)."),
    "video": dict(
        sub="video_marker2022", case="Video gaming and body mass", preset="masem-direct", kind="direct",
        title="MASEMiner evaluation · Video gaming and body mass (Marker et al., 2022)",
        description="Ten primary studies of the meta-analysis by Marker, Gnambs & Appel (2022) on sedentary video "
                    "gaming and body mass, extracted with the MASEMiner direct-information preset (gpt-5.6-sol) and "
                    "then corrected cell by cell to the reference database of the meta-analysis. Each correction is a "
                    "recorded human edit, so the published values equal the reference coding and the audit trail "
                    "shows the model's omissions, over-extractions and wrong values. Held-out validation case of "
                    "Schroeders, Gnambs & Einsiedler (2026)."),
    "tas20": dict(
        sub="TAS20_schroeders2022", case="TAS-20", preset="masem-indirect", kind="indirect",
        title="MASEMiner evaluation · TAS-20 factor structure (Schroeders et al., 2022)",
        description="Ten primary studies of the meta-analytic confirmatory factor analysis of the Toronto Alexithymia "
                    "Scale (Schroeders, Kubera & Gnambs, 2022), extracted with the MASEMiner indirect-information "
                    "preset (gpt-5.6-sol) and then corrected cell by cell to the reference database of the "
                    "meta-analysis. Each correction is a recorded human edit, so the published loadings, factor "
                    "correlations and sample descriptions equal the reference coding. Prompt-development case of "
                    "Schroeders, Gnambs & Einsiedler (2026)."),
    "ncs18": dict(
        sub="NCS18_schroeders2024", case="NCS-18", preset="masem-indirect", kind="indirect",
        title="MASEMiner evaluation · NCS-18 factor structure (Schroeders et al., 2024)",
        description="Ten primary studies of the meta-analysis of the 18-item Need for Cognition Scale (Schroeders, "
                    "Morgenstern, Jankowsky & Gnambs, 2024), extracted with the MASEMiner indirect-information preset "
                    "(gpt-5.6-sol) and then corrected cell by cell to the reference database of the meta-analysis. "
                    "Each correction is a recorded human edit, so the published loadings, factor correlations and "
                    "sample descriptions equal the reference coding. Held-out validation case of Schroeders, Gnambs & "
                    "Einsiedler (2026)."),
}
PRODUCED_BY = "gpt-5.6-sol"
NOTE = "Corrected to the reference database of the meta-analysis (the evaluation's ground truth)."
CONDITION = "without correction"
TOL = 1e-6


def die(msg: str) -> None:
    print(f"! {msg}", file=sys.stderr)
    sys.exit(1)


# ── the exports → the canonical import shape (what the Import page does in the browser) ──────────

F_KEY, R_KEY = re.compile(r"^F(\d+)\.(\d+)$"), re.compile(r"^R(\d+)\.(\d+)$")


def flat_to_rows(obj: dict, kind: str) -> list[dict]:
    """{"F1.3": 0.72} → [{item: 3, factor: 1, loading: 0.72}]; {"R1.2": 0.4} → [{factor_a: 1, factor_b: 2, r: 0.4}]."""
    rows = []
    for k, v in obj.items():
        if v is None:
            continue
        if kind == "loadings":
            m = F_KEY.match(k)
            if not m:
                die(f"unexpected loading key {k!r}")
            rows.append({"item": int(m.group(2)), "factor": int(m.group(1)), "loading": v})
        else:
            m = R_KEY.match(k)
            if not m:
                die(f"unexpected correlation key {k!r}")
            rows.append({"factor_a": int(m.group(1)), "factor_b": int(m.group(2)), "r": v})
    return sorted(rows, key=lambda r: (r.get("factor", r.get("factor_a")), r.get("item", r.get("factor_b"))))


def convert(paper: dict) -> dict:
    """One exported paper → {paper_metadata, samples, evidence}: the curated ``entries`` become the
    samples (loadings and correlations as table rows), the quotes come from the raw reply, re-indexed
    onto the kept samples by sample id, and the per-sample rating is emitted as ``confidence``."""
    raw = paper.get("llm_raw_response") or paper.get("original_model_response")
    raw = json.loads(raw) if isinstance(raw, str) else (raw or {})
    entries = [dict(e) for e in paper["entries"] if isinstance(e, dict)]
    for e in entries:
        if isinstance(e.get("factor_loadings"), dict):
            e["factor_loadings"] = flat_to_rows(e["factor_loadings"], "loadings")
        if isinstance(e.get("factor_correlations"), dict):
            e["factor_correlations"] = flat_to_rows(e["factor_correlations"], "corr")
        rating = e.pop("extraction_confidence", None)
        if isinstance(rating, dict) and "confidence" not in e:
            e["confidence"] = {g: ({"level": v} if isinstance(v, str) else v) for g, v in rating.items()}
    raw_entries = raw.get("samples") or raw.get("entries") or []
    kept = {str(e.get("sample_id")): i for i, e in enumerate(entries) if e.get("sample_id") is not None}
    raw_to_kept = {i: kept[str(e.get("sample_id"))] for i, e in enumerate(raw_entries)
                   if str(e.get("sample_id")) in kept}
    straight = not raw_to_kept and len(raw_entries) == len(entries)
    evidence = []
    for ev in raw.get("evidence") or []:
        fields = ev["field"] if isinstance(ev.get("field"), list) else [ev.get("field")]
        moved = []
        for f in fields:
            if not isinstance(f, str):
                continue
            m = re.match(r"^(samples|entries)\[(\d+)\]", f)
            if not m:
                moved.append(f)
                continue
            k = int(m.group(2)) if straight else raw_to_kept.get(int(m.group(2)))
            if k is None:
                continue                                   # its sample was dropped by the curator
            moved.append(f"samples[{k}]" + f[m.end():])
        if moved:
            evidence.append({**ev, "field": moved[0] if len(moved) == 1 else moved})
    meta = paper.get("paper_metadata") or raw.get("paper_metadata") or {}
    return {"paper_metadata": meta, "samples": entries, "evidence": evidence}


def paper_id(filename: str) -> str:
    """'borkenau1993a.pdf' → 'borkenau1993', 'bolat2017_AE.pdf' → 'bolat2017' (the evaluation's paper id)."""
    s = re.sub(r"\.pdf$", "", filename, flags=re.I)
    s = re.sub(r"_(AE|paper)$", "", s, flags=re.I)
    return re.sub(r"(\d{4})[a-z]$", r"\1", s).lower()


def load_papers(data: Path, sub: str) -> list[dict]:
    papers = []
    for f in sorted((data / "codings_MASEMiner" / sub).glob("*.json")):
        o = json.load(open(f, encoding="utf-8"))
        papers += o["papers"] if "papers" in o else [o]
    return papers


def pdf_index(data: Path) -> dict[str, Path]:
    """Every PDF under 03_data by its (NFC, lower-cased) name — the _AE variants live in the archive."""
    out = {}
    for p in data.rglob("*"):
        if p.suffix.lower() == ".pdf":
            out.setdefault(unicodedata.normalize("NFC", p.name.lower()), p)
    return out


# ── the evaluation's cell keys (ports of 04_syntax/01_functions_evaluation.R) ───────────────────

SO_TRAITS = {"o": 1, "c": 2, "e": 3, "a": 4, "n": 5}


def so_cell_key(var1, var2) -> str:
    v1, v2 = str(var1 or "").strip().lower(), str(var2 or "").strip().lower()
    if not (re.fullmatch(r"[ocean][sp]", v1) and re.fullmatch(r"[ocean][sp]", v2)):
        return "invalid"
    t1, r1, t2, r2 = v1[0], v1[1], v2[0], v2[1]
    if r1 == r2:                                   # same rater: traits in canonical order
        a, b = sorted((t1, t2), key=SO_TRAITS.get)
        return f"{a}{b}{r1}{r1}"
    ts, to = (t1, t2) if r1 == "s" else (t2, t1)   # cross rater: self-rated trait first
    return f"{ts}{to}sp"


def vg_pair_key(var1, var2):
    v = sorted((str(var1 or "").strip().lower(), str(var2 or "").strip().lower()))
    return "-".join(v) if all(x in ("bm", "vg", "pa") for x in v) and v[0] != v[1] else None


KEY_FUN = {"self-other": so_cell_key, "video": vg_pair_key}
UNPLACED: list[str] = []      # reference cells of samples the model never delineated (no record to correct)


def num(s):
    if s is None or s == "" or s == "NA":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def same(a, b) -> bool:
    return a is not None and b is not None and abs(float(a) - float(b)) < TOL


NUMERIC = {"female": float, "age": float, "n": int, "nfac": int, "cfa": int, "met": int}


def gt_value(row: dict, field: str):
    """The reference value of a cell: the number, or the text (country, language) made presentable."""
    v = num(row["value_gt"])
    if v is not None:
        cast = NUMERIC.get(field, float)
        return cast(v) if cast is int else v
    t = row.get("text_gt")
    if t in (None, "", "NA"):
        return None
    t = t.strip()
    return {"usa": "USA", "uk": "UK"}.get(t.lower(), t.title())


# ── planning the corrections of one paper ───────────────────────────────────────────────────────

def load_deviations(results: Path, case: str) -> dict[tuple[str, str], list[dict]]:
    """{(paper, sample_llm): [cells]} for every non-TP cell of the unverified output of this case."""
    out = defaultdict(list)
    with open(results / "MASEMiner_vs_ground_truth_cells.csv", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if r["condition"] == CONDITION and r["case"] == case and r["outcome"] != "TP":
                out[(r["paper"], r["sample_llm"])].append(r)
    return out


class Plan:
    """The corrections of one record: the new field values and the diff that explains each change."""

    def __init__(self, fv: dict):
        self.fv = copy.deepcopy(fv)
        self.diff: list[dict] = []
        self.unmatched: list[str] = []
        self.removed: set = set()          # row ids / positions already dropped
        self.ops = Counter()

    def change(self, path: str, old, new) -> None:
        self.diff.append({"field_path": path, "original_value": old, "final_value": new})


def plan_direct(plan: Plan, cells: list[dict], key_fun) -> None:
    fv = plan.fv
    rows = fv.get("records") or []
    consumed = set()

    def find(key, pick):
        for j, r in enumerate(rows):
            if j in consumed or j in plan.removed:
                continue
            if key_fun(r.get("var1"), r.get("var2")) == key and pick(r):
                return j
        return None

    # effect sizes first: an over-extracted row goes, and takes its sample size with it
    for c in [c for c in cells if c["variable"] == "effect_size"]:
        key, out, v_llm, v_gt = c["key"], c["outcome"], num(c["value_llm"]), num(c["value_gt"])
        if out == "overextraction":
            j = find(key, lambda r: same(r.get("es"), v_llm))
            if j is None:
                plan.unmatched.append(f"effect_size over {key}={v_llm}")
                continue
            r = rows[j]
            plan.change(f"records[{j}]", f"{r.get('var1')} × {r.get('var2')}: es = {r.get('es')}",
                        "(row removed — not a target of the meta-analysis)")
            plan.removed.add(j); plan.ops["effect_size/overextraction"] += 1
        elif out == "wrong_value":
            j = find(key, lambda r: same(r.get("es"), v_llm))
            if j is None:
                plan.unmatched.append(f"effect_size wrong {key}={v_llm}")
                continue
            plan.change(f"records[{j}].es", rows[j].get("es"), v_gt); rows[j]["es"] = v_gt
            consumed.add(j); plan.ops["effect_size/wrong_value"] += 1
        elif out == "omission":
            var1, var2 = (key[0] + key[2], key[1] + key[3]) if len(key) == 4 else key.split("-")
            rows.append({"var1": var1, "var2": var2, "es": v_gt, "type": (c.get("type") or None)})
            plan.change(f"records[{len(rows) - 1}]", None, f"(row added: {var1} × {var2}, es = {v_gt})")
            plan.ops["effect_size/omission"] += 1
    consumed.clear()
    for c in [c for c in cells if c["variable"] == "sample_size"]:
        key, out, v_llm, v_gt = c["key"], c["outcome"], num(c["value_llm"]), num(c["value_gt"])
        if out == "overextraction":
            if any(key_fun(rows[j].get("var1"), rows[j].get("var2")) == key and same(rows[j].get("n"), v_llm)
                   for j in plan.removed):
                plan.ops["sample_size/overextraction"] += 1
                continue
            j = find(key, lambda r: same(r.get("n"), v_llm))
            if j is None:
                plan.unmatched.append(f"sample_size over {key}={v_llm}")
                continue
            plan.change(f"records[{j}].n", rows[j].get("n"), None); rows[j]["n"] = None
            consumed.add(j); plan.ops["sample_size/overextraction"] += 1
        elif out in ("omission", "wrong_value"):
            j = find(key, (lambda r: r.get("n") is None) if out == "omission" else (lambda r: same(r.get("n"), v_llm)))
            if j is None:
                plan.unmatched.append(f"sample_size {out} {key}={v_llm}→{v_gt}")
                continue
            plan.change(f"records[{j}].n", rows[j].get("n"), int(v_gt)); rows[j]["n"] = int(v_gt)
            consumed.add(j); plan.ops[f"sample_size/{out}"] += 1
    # reliabilities are compared as value sets per variable: a changed value shows up as an
    # omission (the reference value) plus an over-extraction (the model's) for the same key
    rel_cells = [c for c in cells if c["variable"] == "reliability"]
    by_key = defaultdict(lambda: {"omission": [], "overextraction": []})
    for c in rel_cells:
        if c["outcome"] in ("omission", "overextraction"):
            by_key[c["key"].lower()][c["outcome"]].append(c)

    def rel_cells_of(key, pred):
        """(j, rel) of every live row carrying variable ``key`` whose reliability satisfies ``pred``."""
        return [(j, rel) for j, r in enumerate(rows) if j not in plan.removed
                for var, rel in (("var1", "rel1"), ("var2", "rel2"))
                if str(r.get(var) or "").lower() == key and pred(r.get(rel))]

    def removed_rel(key, v):
        return any(str(rows[j].get(var) or "").lower() == key and same(rows[j].get(rel), v)
                   for j in plan.removed for var, rel in (("var1", "rel1"), ("var2", "rel2")))

    for key, pair in by_key.items():
        while pair["omission"] and pair["overextraction"]:        # a value the model got wrong
            o, x = pair["omission"].pop(0), pair["overextraction"].pop(0)
            v_gt, v_llm = num(o["value_gt"]), num(x["value_llm"])
            hits = rel_cells_of(key, lambda v: same(v, v_llm))
            if not hits:
                plan.unmatched.append(f"reliability {key} {v_llm}→{v_gt}"); continue
            for j, rel in hits:
                plan.change(f"records[{j}].{rel}", rows[j][rel], v_gt); rows[j][rel] = v_gt
            plan.ops["reliability/omission"] += 1; plan.ops["reliability/overextraction"] += 1
        for o in pair["omission"]:                                 # the reference has it, the model has nothing
            v_gt = num(o["value_gt"])
            hits = rel_cells_of(key, lambda v: v is None)
            if not hits:
                plan.unmatched.append(f"reliability omission {key}={v_gt}: no row carries {key}"); continue
            for j, rel in hits:
                plan.change(f"records[{j}].{rel}", None, v_gt); rows[j][rel] = v_gt
            plan.ops["reliability/omission"] += 1
        for x in pair["overextraction"]:                           # the model has it, the reference has nothing
            v_llm = num(x["value_llm"])
            hits = rel_cells_of(key, lambda v: same(v, v_llm))
            if not hits and removed_rel(key, v_llm):
                plan.ops["reliability/overextraction"] += 1; continue   # went with its row
            if not hits:
                plan.unmatched.append(f"reliability over {key}={v_llm}"); continue
            for j, rel in hits:
                plan.change(f"records[{j}].{rel}", rows[j][rel], None); rows[j][rel] = None
            plan.ops["reliability/overextraction"] += 1
    for c in rel_cells:
        if c["outcome"] == "wrong_value":
            key, v_llm, v_gt = c["key"].lower(), num(c["value_llm"]), num(c["value_gt"])
            hits = rel_cells_of(key, lambda v: same(v, v_llm))
            if not hits:
                plan.unmatched.append(f"reliability wrong {key} {v_llm}→{v_gt}"); continue
            for j, rel in hits:
                plan.change(f"records[{j}].{rel}", rows[j][rel], v_gt); rows[j][rel] = v_gt
            plan.ops["reliability/wrong_value"] += 1
    plan_descriptives(plan, cells, {"country", "female", "age"})


def plan_indirect(plan: Plan, cells: list[dict]) -> None:
    fv = plan.fv
    for table, pattern, cols, val in (("factor_loadings", F_KEY, ("factor", "item"), "loading"),
                                      ("factor_correlations", R_KEY, ("factor_a", "factor_b"), "r")):
        rows = fv.get(table) or []
        for c in [c for c in cells if c["variable"] == table]:
            m = pattern.match(c["key"])
            if not m:
                plan.unmatched.append(f"{table} key {c['key']!r}"); continue
            a, b = int(m.group(1)), int(m.group(2))
            out, v_llm, v_gt = c["outcome"], num(c["value_llm"]), num(c["value_gt"])
            j = next((j for j, r in enumerate(rows) if r.get(cols[0]) == a and r.get(cols[1]) == b), None)
            if out == "omission":
                if j is not None:
                    plan.unmatched.append(f"{table} omission {c['key']} but a row exists"); continue
                rows.append({cols[0]: a, cols[1]: b, val: v_gt}); fv[table] = rows
                plan.change(f"{table}[{len(rows) - 1}]", None, f"(row added: {c['key']} = {v_gt})")
            elif out == "wrong_value":
                if j is None and v_llm == 0:                     # unprinted: the evaluation reads it as 0
                    rows.append({cols[0]: a, cols[1]: b, val: v_gt}); fv[table] = rows
                    plan.change(f"{table}[{len(rows) - 1}]", 0, f"(row added: {c['key']} = {v_gt})")
                elif j is None or not same(rows[j].get(val), v_llm):
                    plan.unmatched.append(f"{table} wrong {c['key']}={v_llm}"); continue
                else:
                    plan.change(f"{table}[{j}].{val}", rows[j].get(val), v_gt); rows[j][val] = v_gt
            elif out == "overextraction":
                if j is None:
                    plan.unmatched.append(f"{table} over {c['key']}"); continue
                plan.change(f"{table}[{j}]", f"{c['key']} = {rows[j].get(val)}", "(row removed)")
                plan.removed.add((table, j))
            plan.ops[f"{table}/{out}"] += 1
    plan_descriptives(plan, cells, {"country", "female", "age", "lang", "cfa", "nfac", "met", "n"})


def plan_descriptives(plan: Plan, cells: list[dict], fields: set) -> None:
    fv = plan.fv
    for c in [c for c in cells if c["variable"] in fields]:
        field, out = c["variable"], c["outcome"]
        new = None if out == "overextraction" else gt_value(c, field)
        if out != "overextraction" and new is None:
            plan.unmatched.append(f"{field} {out}: no reference value"); continue
        plan.change(field, fv.get(field), new); fv[field] = new
        plan.ops[f"{field}/{out}"] += 1


def finish(plan: Plan) -> dict:
    """Drop the rows marked removed (by position, from the end) and return the field values to store."""
    fv = plan.fv
    if plan.removed:
        if all(isinstance(x, int) for x in plan.removed):
            fv["records"] = [r for j, r in enumerate(fv.get("records") or []) if j not in plan.removed]
        else:
            for table, j in sorted(plan.removed, key=lambda t: -t[1]):
                del fv[table][j]
    return fv


def plan_paper(case_key: str, cells_by_sample: dict, paper: str, samples: list[dict]) -> list[Plan]:
    """One Plan per sample (entry), in entry order; samples the cells file never names get an empty plan."""
    plans = []
    for (p, s), cs in cells_by_sample.items():
        if p == paper and (s in ("NA", "") or "#" not in s):
            UNPLACED.extend(f"{paper} {c['sample_gt']}: {c['variable']} {c['outcome']} = {c['value_gt'] if c['value_gt'] != 'NA' else c['text_gt']}" for c in cs)
    for i, fv in enumerate(samples):
        plan = Plan(fv)
        cells = cells_by_sample.get((paper, f"{paper}#{i + 1}"), [])
        if cells:
            if CASES[case_key]["kind"] == "direct":
                plan_direct(plan, cells, KEY_FUN[case_key])
            else:
                plan_indirect(plan, cells)
        plans.append(plan)
    return plans


# ── the server side ──────────────────────────────────────────────────────────────────────────────

def login(c: httpx.Client, email: str, password: str) -> None:
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        die(f"login failed: {r.status_code} {r.text[:200]}")


def schema_ids(c: httpx.Client) -> dict:
    return c.get("/api/version").json()["presets"]


def own_dataset(c: httpx.Client, title: str, description: str, schema_id: str) -> tuple[str, str]:
    for d in c.get("/api/datasets").json()["datasets"]:
        if d["title"] == title and d.get("schema_id") == schema_id:
            return d["id"], "reused"
    r = c.post("/api/datasets", json={"title": title, "description": description, "schema_id": schema_id,
                                      "visibility": "private"})
    if r.status_code != 200:
        die(f"dataset create failed: {r.status_code} {r.text[:300]}")
    return r.json()["id"], "created"


def in_dataset(c: httpx.Client, dataset_id: str) -> set:
    return {d.get("filename") for d in c.get("/api/documents", params={"dataset": dataset_id, "limit": 5000}).json()["documents"]}


def ingest(c: httpx.Client, pdf: Path, result: dict, schema_id: str, dataset_id: str) -> dict:
    r = c.post("/api/ingest-pdf", data={"result": json.dumps(result), "schema_id": schema_id, "dataset_id": dataset_id,
                                        "produced_by": PRODUCED_BY},
               files={"pdf": (pdf.name, pdf.read_bytes(), "application/pdf")})
    if r.status_code != 200:
        die(f"import of {pdf.name} failed: {r.status_code} {r.text[:300]}")
    return r.json()


def verify(c: httpx.Client, record_id: str, body: dict) -> None:
    r = c.post(f"/api/records/{record_id}/verify", json=body)
    if r.status_code != 200:
        die(f"verify {record_id} failed: {r.status_code} {r.text[:300]}")


def run_case(c: httpx.Client | None, key: str, data: Path, results: Path, pdfs: dict, dry: bool) -> None:
    cfg = CASES[key]
    print(f"\n== {cfg['title']}")
    papers = load_papers(data, cfg["sub"])
    cells = load_deviations(results, cfg["case"])
    expected = Counter(f"{c['variable']}/{c['outcome']}" for v in cells.values() for c in v)
    planned, unmatched, n_samples = Counter(), [], 0
    dataset_id = schema_id = None
    have = set()
    if not dry:
        sid = schema_ids(c).get(cfg["preset"])
        if not sid:
            die(f"the server has no preset {cfg['preset']}")
        schema_id = sid
        dataset_id, how = own_dataset(c, cfg["title"], cfg["description"], schema_id)
        print(f"  dataset {how}: {c.base_url}/dataset?id={dataset_id}")
        have = in_dataset(c, dataset_id)
    for paper in papers:
        fn = paper["filename"]
        pdf = pdfs.get(unicodedata.normalize("NFC", fn.lower()))
        if pdf is None:
            print(f"  ! {fn}: no PDF found under 03_data — skipped"); continue
        result = convert(paper)
        pid = paper_id(fn)
        plans = plan_paper(key, cells, pid, result["samples"])
        n_samples += len(plans)
        for p in plans:
            planned.update(p.ops); unmatched += [f"{pid}: {u}" for u in p.unmatched]
        n_corr = sum(1 for p in plans if p.diff)
        if dry:
            print(f"  ✓ {pid:16s} {len(result['samples'])} sample(s), {len(result['evidence']):2d} quotes, pdf {pdf.name:24s} corrections on {n_corr} sample(s): {sum(len(p.diff) for p in plans)} cells")
            continue
        if fn in have:
            print(f"  · {pid}: already in the dataset"); continue
        out = ingest(c, pdf, result, schema_id, dataset_id)
        view = c.get(f"/api/documents/{out['document_id']}/view").json()
        recs = sorted(view["records"], key=lambda r: r["entry_index"])
        if len(recs) != len(plans):
            die(f"{pid}: {len(recs)} records stored but {len(plans)} samples planned")
        for rec, plan in zip(recs, plans):
            if plan.diff:
                stored = rec["field_values"]
                fv = finish(plan)
                # keep the row ids the import assigned (evidence refers to rows by id)
                for table in ("records", "factor_loadings", "factor_correlations"):
                    if table in fv and table in stored:
                        ids = {json.dumps(contract_key(r), sort_keys=True): r.get("_rid") for r in stored[table]}
                        for r in fv[table]:
                            rid = ids.get(json.dumps(contract_key(r), sort_keys=True))
                            if rid:
                                r["_rid"] = rid
                verify(c, rec["id"], {"status": "corrected", "field_values": fv, "diff": plan.diff, "notes": NOTE})
            verify(c, rec["id"], {"status": "verified"})
        print(f"  ✓ {pid:16s} {out['n_records']} record(s), {out['n_highlights']} quote(s) located, {out['n_pages']} pages; "
              f"{sum(len(p.diff) for p in plans)} corrected cell(s), all verified")
    print(f"  samples: {n_samples} | corrections planned: {sum(planned.values())} cells")
    for k2 in sorted(set(expected) | set(planned)):
        flag = "" if expected.get(k2, 0) == planned.get(k2, 0) else "   <-- differs from the cells file"
        print(f"    {k2:42s} cells {expected.get(k2, 0):4d}  planned {planned.get(k2, 0):4d}{flag}")
    if unmatched:
        print(f"  ! {len(unmatched)} deviation(s) could not be located:")
        for u in unmatched[:40]:
            print("     ", u)
    if UNPLACED:
        print(f"  · {len(UNPLACED)} reference value(s) belong to samples the model did not delineate — not representable as a correction:")
        for u in UNPLACED[:40]:
            print("     ", u)
        UNPLACED.clear()
    if not dry:
        d = c.get(f"/api/datasets/{dataset_id}").json()
        print(f"  credibility: {(d.get('credibility') or {}).get('label')}")


def contract_key(row: dict) -> dict:
    """What identifies a row across the correction: its identity columns, never the edited value."""
    return {k: row.get(k) for k in ("var1", "var2", "desc1", "desc2", "item", "factor", "factor_a", "factor_b") if k in row}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True, type=Path, help="the 03_data folder")
    ap.add_argument("--results", required=True, type=Path, help="the 05_results folder (cells csv)")
    ap.add_argument("--base", default="https://metalens.tech")
    ap.add_argument("--email")
    ap.add_argument("--only", help="comma-separated: self-other,video,tas20,ncs18")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    keys = [k.strip() for k in (args.only or ",".join(CASES)).split(",") if k.strip()]
    for k in keys:
        if k not in CASES:
            die(f"unknown case {k!r}; choose from {', '.join(CASES)}")
    if not (args.results / "MASEMiner_vs_ground_truth_cells.csv").exists():
        die(f"{args.results} holds no MASEMiner_vs_ground_truth_cells.csv")
    pdfs = pdf_index(args.data)
    if args.dry_run:
        for k in keys:
            run_case(None, k, args.data, args.results, pdfs, dry=True)
        return
    if not args.email:
        die("--email is required for a live run")
    with httpx.Client(base_url=args.base.rstrip("/"), timeout=httpx.Timeout(600.0, connect=30.0)) as c:
        login(c, args.email, os.environ.get("PAPERLENS_PASSWORD") or getpass.getpass(f"password for {args.email}: "))
        for k in keys:
            run_case(c, k, args.data, args.results, pdfs, dry=False)
        c.post("/api/auth/logout")


if __name__ == "__main__":
    main()
