"""The abstract's claims mapped to the record — the ``claims`` pass, a port of the review
project's ``claims.py`` ``run``. Two calls per paper: (1) the abstract's sentences (split in
code: A1, A2 …), each classified as result, method, background, implication or other, with
the numbers it states and the introduction sentences (I1, I2 …) that restate it; (2) every
result claim against the record's estimates (pass c under pass a, numbers as printed): the
estimates that support it and how far (exact, consistent, partial, none). Then in code: every
number a claim states is looked up among the printed numbers of the mapped estimates. The
abstract is the PDF's own (located by the paper's abstract as the engine knows it, else from
an "Abstract" line), the engine's when the PDF prints none; the introduction runs from the
abstract's end to the first section heading set as one. The pass reads the primary estimands
and estimates and canonicalises their ids on copies, as the project's files were.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from . import hashes
from . import passes as PS
from . import record as R
from . import text as X

STOP = {"methods", "results", "appendix", "literature", "discussion"}
MIN_INTRO = 1500
MAX_INTRO = 30000
MAX_ABSTRACT = 3000
PAGE = re.compile(r"=== PAGE (\d+)")
EMAIL = re.compile(r"\S+@\S+\.\w+")
FOOTNOTE = re.compile(r"^\s*[*†‡§¶]")
BOILERPLATE = re.compile(r"^(Open Access|Funding:|Supplemental Material|Copyright|History:|Keywords?\b|JEL\b|©|Received\b|Accepted\b|Description\b|Editor.s summary)", re.I)
SHORT = 45
MAX_I = 900
KINDS = ("result", "method", "background", "implication", "other")
COVERAGE = ("exact", "consistent", "partial", "none")
NUMFIELDS = ("effect_reported", "effect_value", "effect_type", "effect_units", "se", "ci_low", "ci_high", "p_value", "p_reported",
             "statistic", "n_ref", "mean_ref", "sd_ref", "n_treat", "mean_treat", "sd_treat", "n_obs")
NUMTOK = re.compile(r"(?<![A-Za-z\d.])[-−–]?\d[\d,]*(?:\.\d+)?%?")
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
         "twelve": 12, "half": 0.5, "twice": 2, "double": 2, "doubled": 2, "doubles": 2, "triple": 3, "tripled": 3, "thrice": 3, "quarter": 0.25}
MAX_TOKENS = 16_000
norm = X.norm


# ── the text ────────────────────────────────────────────────────────────────────────────────

def pages_of(lines):
    out, page = [], 0
    for l in lines:
        m = PAGE.match(l)
        if m:
            page = int(m.group(1))
        out.append(page)
    return out


def _find(lines, needle, lo, hi):
    """The first line index in [lo, hi) whose normalised text (or its join with the next line) holds `needle`; (index, spans_two)."""
    if not needle:
        return None, False
    for k in range(lo, min(hi, len(lines))):
        if needle in norm(lines[k]):
            return k, False
    for k in range(lo, min(hi, len(lines) - 1)):
        if needle in norm(lines[k] + lines[k + 1]):
            return k, True
    return None, False


def abstract_end(lines, start, limit=MAX_ABSTRACT):
    """The last line of an abstract that starts at `start`."""
    chars = 0
    for j in range(start + 1, min(len(lines), start + 400)):
        if PAGE.match(lines[j]):
            continue
        s = lines[j].strip()
        chars += len(s) + 1
        m = X.HEADING.match(s) if s and len(s) <= 70 and s[0].isalnum() else None
        nxt = lines[j + 1].strip() if j + 1 < len(lines) else ""
        short = lambda x: 0 < len(x) <= SHORT and not re.search(r"[.?!”\")]$", x)  # noqa: E731
        if (m and X.classify(m.group("name")) and chars > 200) or re.match(r"^(keywords?|jel\b)", s, re.I) or FOOTNOTE.match(s) \
                or (chars > 200 and (EMAIL.search(s) or not s or (short(s) and short(nxt)))) or chars > limit:
            return j - 1
    return min(len(lines) - 1, start + 60)


def abstract_block(lines, pool_abstract):
    """(i, j, how): the lines that print the abstract, located by the known abstract's first and
    last words ("anchored"), from its first words to an end marker ("head"), or from an
    'Abstract' line ("label"); None when none is found."""
    if pool_abstract:
        words = pool_abstract.split()
        head, tail = norm(" ".join(words[:6])), norm(" ".join(words[-6:]))
        i, _ = _find(lines, head, 0, 600)
        if i is not None:
            label = next((k for k in range(i, max(-1, i - 12), -1) if X.ABSTRACT.match(lines[k].strip())), None)
            j, two = _find(lines, tail, i, i + 80)
            if j is not None:
                return (label if label is not None else i), j + (1 if two else 0), "anchored"
            i = label if label is not None else i
            return i, abstract_end(lines, i, min(MAX_ABSTRACT, int(1.6 * len(pool_abstract)) + 300)), "head"
    start = next((k for k, l in enumerate(lines[:400]) if X.ABSTRACT.match(l.strip())), None)
    if start is None:
        return None
    return start, abstract_end(lines, start), "label"


def strong(s):
    """A heading set as one: the numbering stripped, every word of four letters or more capitalised."""
    name = re.sub(r"^\s*(?:(?:[1-9]\d?|[IVX]{1,4}|[A-H])(?:\.\d+)*\.?\s+)?", "", s).strip()
    words = [w for w in re.findall(r"[A-Za-z]+", name) if len(w) >= 4]
    return bool(words) and all(w[0].isupper() for w in words)


def intro_block(lines, heads, j):
    """(start, end, how): from the line after the abstract to the line before the first strong
    methods / results / literature / discussion / appendix heading at least MIN_INTRO characters on."""
    marks = {i: (k, s) for i, k, s in heads}
    start, chars = j + 1, 0
    for k in range(start, len(lines)):
        chars += len(lines[k]) + 1
        if k in marks and marks[k][0] in STOP and strong(marks[k][1]) and chars >= MIN_INTRO:
            return start, k - 1, f"heading: {marks[k][1][:40]}"
        if chars > MAX_INTRO:
            return start, k, "cap"
    return start, len(lines) - 1, "end of text"


def cut(raw: str, pool_abstract: str):
    """The abstract's sentences and the introduction's, each with its page, and where they came from."""
    lines = raw.splitlines()
    pages = pages_of(lines)
    _, heads = X.scan(raw)
    blk = abstract_block(lines, pool_abstract)
    if blk:
        i, j, how = blk
        keep, foot_page = [], None
        for k in range(i, j + 1):
            if PAGE.match(lines[k]):
                continue
            if FOOTNOTE.match(lines[k]):
                foot_page = pages[k]
            if pages[k] != foot_page:
                keep.append(X.ABSTRACT.sub("", lines[k].strip(), count=1) if k == i else lines[k])
        text = X.page_text(keep)
        abstract = {"source": "pdf", "how": how, "page": pages[i], "lines": [i, j], "text": text}
        if pool_abstract and not re.search(r"[.?!”\")]\s*$", text):
            abstract = {"source": "pool", "how": f"pdf abstract cut short at line {j} ({len(text)} of {len(pool_abstract)} characters)",
                        "page": pages[i], "lines": [i, j], "text": " ".join(pool_abstract.split())}
    elif pool_abstract:
        abstract = {"source": "pool", "how": "pdf abstract not found", "page": None, "lines": None, "text": " ".join(pool_abstract.split())}
        j = next((k for k in range(len(lines)) if pages[k] >= 2), 0) - 1
    else:
        return {"abstract": None, "sentences": [], "intro": None, "intro_sentences": []}
    a_sents = []
    for s in X.split_sentences(abstract["text"]):
        if BOILERPLATE.match(s):
            break
        if len(s) >= 15:
            a_sents.append({"id": f"A{len(a_sents) + 1}", "page": abstract["page"], "text": s})
    start, end, how = intro_block(lines, heads, j)
    whole: dict = {}
    for k, l in enumerate(lines):
        if not PAGE.match(l):
            whole.setdefault(pages[k], []).append(l)
    kept = {p: set(ls) for p, ls in X.without_running_lines(whole).items()}
    by_page: dict = {}
    for k in range(start, end + 1):
        if not PAGE.match(lines[k]) and lines[k] in kept.get(pages[k], ()):
            by_page.setdefault(pages[k], []).append(lines[k])
    i_sents, seen = [], set()
    in_abstract = norm(abstract["text"])
    for p in sorted(by_page):
        for s in X.split_sentences(X.page_text(by_page[p])):
            if not X.MIN_LEN <= len(s) <= MAX_I:
                continue
            key = re.sub(r"\W+", "", s.lower())[:120]
            if key in seen or norm(s) in in_abstract:
                continue
            seen.add(key)
            i_sents.append({"id": f"I{len(i_sents) + 1}", "page": p, "text": s})
    intro = {"lines": [start, end], "pages": [pages[start], pages[end]], "how": how, "chars": sum(len(lines[k]) + 1 for k in range(start, end + 1))}
    return {"abstract": abstract, "sentences": a_sents, "intro": intro, "intro_sentences": i_sents}


# ── the texts of the calls ──────────────────────────────────────────────────────────────────

def step_hash(prompt, schema):
    return hashlib.sha256(json.dumps([prompt, schema], sort_keys=True).encode()).hexdigest()[:8]


def h8(s):
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def cache_key(name: str, user: str) -> str:
    """The project's cache key of a call: ``abstract:<step>:<user>`` / ``map:<step>:<user>``."""
    pn = {"abstract": "claims_abstract", "map": "claims_map"}[name]
    return f"{name}:{step_hash(hashes.prompt(pn), hashes.schema(pn))}:{h8(user)}"


def abstract_text(c, title):
    out = [f"Title: {title}", "", "# The abstract"] + [f"{s['id']}: {s['text']}" for s in c["sentences"]]
    out += ["", "# The introduction"] + [f"{s['id']} (p. {s['page']}): {s['text']}" for s in c["intro_sentences"]]
    return "\n".join(out)


def record_text(es, em):
    by: dict = {}
    for x in em.get("estimates") or []:
        by.setdefault(x.get("estimand_id"), []).append(x)
    out = ["## Tasks and arms", PS.tasks_arms_text(es), "## Outcomes"]
    for o in es.get("outcomes") or []:
        out.append(f"- {o['outcome_id']}: {o['measure']} [{o.get('unit')}], better = {o.get('direction')}" + (f"; kind {o['measure_kind']}" if o.get("measure_kind") else ""))
    out.append("## Estimands and their estimates (numbers as printed)")
    for e in es.get("estimands") or []:
        out.append(f"### {e['estimand_id']}: {e['statement']} (subgroup: {e.get('subgroup')}; horizon: {e.get('horizon')})")
        for x in by.get(e["estimand_id"]) or []:
            nums = ", ".join(f"{k} = {x[k]}" for k in NUMFIELDS if x.get(k) is not None)
            out.append(f"- estimate `{x['estimate_id']}`: role {x.get('role')}; source {R._source(x.get('source'))}; specification: {x.get('specification')}; {nums}")
        if not by.get(e["estimand_id"]):
            out.append("- (no estimate in the record)")
    if em.get("unmatched_estimands"):
        out.append("## Estimands without a number: " + "; ".join(f"{u['estimand_id']} ({u['why']})" for u in em["unmatched_estimands"]))
    return "\n".join(out)


def claims_text(sentences):
    out = []
    for s in sentences:
        if s["kind"] != "result":
            continue
        out.append(f"### {s['id']}: “{s['text']}”" + (f" — numbers stated: {', '.join(s['numbers'])}" if s["numbers"] else " — no number stated"))
        out += [f"- {it['id']} (p. {it['page']}): “{it['text']}”" for it in s["intro"]] or ["- (not restated in the introduction)"]
    return "\n".join(out)


# ── the checks in code ──────────────────────────────────────────────────────────────────────

def numbers_in(text, years=False):
    """The quantities a run of text states, as (token, absolute value, decimals)."""
    out = []
    for m in NUMTOK.finditer(text):
        tok = m.group(0).replace(",", "").rstrip("%").lstrip("-−–")
        try:
            v = float(tok)
        except ValueError:
            continue
        if not years and v.is_integer() and 1950 <= v <= 2049:
            continue
        out.append((m.group(0), v, len(tok.split(".")[1]) if "." in tok else 0))
    for w in re.findall(r"[A-Za-z]+", text.lower()):
        if w in WORDS:
            out.append((w, float(WORDS[w]), 2 if WORDS[w] != int(WORDS[w]) else 0))
    return out


def printed_numbers(est):
    """{value: where} over the estimate's printed fields and the quotes it was read from."""
    vals: dict = {}
    for k in NUMFIELDS:
        v = est.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            vals.setdefault(abs(float(v)), k)
        elif isinstance(v, str):
            for _, x, _ in numbers_in(v, years=True):
                vals.setdefault(x, k)
    for k in ("quote", "text_quote"):
        for _, x, _ in numbers_in(est.get(k) or "", years=True):
            vals.setdefault(x, k)
    return vals


def number_checks(claim, ests):
    """For every number the claim states: the estimate among the mapped ones that prints it, exactly or once rounded."""
    stated = [(tok, v, d, "abstract") for n in claim["numbers"] for tok, v, d in numbers_in(n)]
    for it in claim["intro"]:
        stated += [(tok, v, d, it["id"]) for tok, v, d in numbers_in(it["text"])]
    out, seen = [], set()
    for tok, v, d, src in stated:
        if (v, src) in seen:
            continue
        seen.add((v, src))
        hit = {"number": tok, "value": v, "from": src, "found": None, "in": None, "field": None}
        for e in ests:
            vals = printed_numbers(e)
            exact = next((k for x, k in vals.items() if abs(x - v) < 1e-9), None)
            rounded = exact or next((k for x, k in vals.items() if abs(round(x, d) - v) < 1e-9), None)
            if exact or rounded:
                hit.update({"found": "exact" if exact else "rounded", "in": e["estimate_id"], "field": exact or rounded})
                break
        out.append(hit)
    return out


def resolve_ids(given, ests):
    """(estimate ids, unknown ids, estimand ids resolved)."""
    lower = {e.lower(): e for e in ests}
    by_estimand: dict = {}
    for e, x in ests.items():
        by_estimand.setdefault(str(x.get("estimand_id")).lower(), []).append(e)
    out, unknown, resolved = [], [], []
    for g in given:
        g = str(g).strip().strip("`")
        if g in ests:
            hit = [g]
        elif g.lower() in lower:
            hit = [lower[g.lower()]]
        elif g.lower() in by_estimand:
            es_ = by_estimand[g.lower()]
            hit = [e for e in es_ if ests[e].get("role") == "main"] or es_
            resolved.append(g)
        else:
            unknown.append(g)
            continue
        out += [e for e in hit if e not in out]
    return out, unknown, resolved


# ── the pass ────────────────────────────────────────────────────────────────────────────────

def canon_reads(ctx) -> tuple[dict, dict]:
    """The primary estimands and estimates with the derivations and the canonical ids applied, on copies."""
    es, em = copy.deepcopy(ctx.read("estimands")), copy.deepcopy(ctx.read("estimates"))
    PS.derive_findings(es); PS.derive_contrast(es); PS.derive_roles(em)
    PS.canon_ids(es, em, {})
    return es, em


def claims(ctx) -> dict:
    es, em = canon_reads(ctx)
    ests = {x["estimate_id"]: x for x in em.get("estimates") or []}
    title = ctx.title or ""
    P = PS.pages_of(ctx)
    c = cut(P.raw, (ctx.paper.get("abstract") or ""))
    if c is None or not c["sentences"]:
        ctx.log("claims: no abstract found")
        return {"status": "no_abstract", "abstract": (c or {}).get("abstract"), "intro": (c or {}).get("intro"), "sentences": [], "intro_sentences": (c or {}).get("intro_sentences") or [],
                "estimates_claimed": {}, "main_unclaimed": [e for e, x in ests.items() if x.get("role") == "main"], "counts": {}}
    u1 = abstract_text(c, title)
    a1 = ctx.call(hashes.prompt("claims_abstract"), u1, hashes.schema("claims_abstract"), max_tokens=MAX_TOKENS)
    isent = {s["id"]: s for s in c["intro_sentences"]}
    answered = {r["id"]: r for r in a1.get("sentences") or []}
    sentences = []
    for s in c["sentences"]:
        r = answered.get(s["id"]) or {}
        kind = r.get("kind") if r.get("kind") in KINDS else ("other" if r else "unanswered")
        intro = [isent[i] for i in r.get("intro_ids") or [] if i in isent]
        sentences.append({**s, "kind": kind, "numbers": [n for n in r.get("numbers") or [] if n], "has_number": bool(r.get("numbers")),
                          "intro": intro, "intro_has_number": any(numbers_in(it["text"]) for it in intro), "note": r.get("note"),
                          "estimate_ids": [], "coverage": None, "why": None, "number_checks": []})
    results = [s for s in sentences if s["kind"] == "result"]
    ctx.log(f"claims: {len(sentences)} sentences, {len(results)} results ({sum(1 for s in results if not s['has_number'])} without a number)")
    if results:
        u2 = f"Title: {title}\n\n# The claims\n\n{claims_text(sentences)}\n\n# The record\n\n{record_text(es, em)}"
        a2 = ctx.call(hashes.prompt("claims_map"), u2, hashes.schema("claims_map"), max_tokens=MAX_TOKENS)
        mapped = {r["id"]: r for r in a2.get("claims") or []}
        for s in results:
            r = mapped.get(s["id"]) or {}
            s["estimate_ids"], s["unknown_estimate_ids"], s["from_estimands"] = resolve_ids(r.get("estimate_ids") or [], ests)
            s["coverage"] = r.get("coverage") if r.get("coverage") in COVERAGE else ("none" if not s["estimate_ids"] else "consistent")
            s["why"] = r.get("why")
            s["number_checks"] = number_checks(s, [ests[e] for e in s["estimate_ids"]])
            found = [h for h in s["number_checks"] if h["found"]]
            s["numbers_found"] = f"{len(found)}/{len(s['number_checks'])}" if s["number_checks"] else None
            s["flag"] = ("exact, but no stated number is printed in the mapped estimates" if s["coverage"] == "exact" and s["number_checks"] and not found
                         else "every stated number printed, but not called exact" if s["coverage"] != "exact" and s["number_checks"] and len(found) == len(s["number_checks"])
                         else None)
    claimed: dict = {}
    for s in results:
        for e in s["estimate_ids"]:
            claimed.setdefault(e, []).append(s["id"])
    cov = {k: sum(1 for s in results if s["coverage"] == k) for k in COVERAGE}
    ctx.log("claims: " + ", ".join(f"{v} {k}" for k, v in cov.items()) + f"; {len(claimed)} estimates claimed")
    return {"status": "done", "abstract": c["abstract"], "intro": c["intro"], "sentences": sentences, "intro_sentences": c["intro_sentences"],
            "estimates_claimed": claimed, "main_unclaimed": [e for e, x in ests.items() if x.get("role") == "main" and e not in claimed],
            "counts": {"sentences": len(sentences), "results": len(results), "with_number": sum(1 for s in results if s["has_number"]),
                       "without_number": sum(1 for s in results if not s["has_number"]),
                       "intro_gives_number": sum(1 for s in results if not s["has_number"] and s["intro_has_number"]),
                       "restated": sum(1 for s in results if s["intro"]), **cov,
                       "numbers_stated": sum(len(s["number_checks"]) for s in results),
                       "numbers_found": sum(1 for s in results for h in s["number_checks"] if h["found"])}}


def registrations() -> dict[str, dict]:
    from . import taxonomy as T
    return {
        "claims": {"kind": "pass", "label": "The abstract's claims against the record", "calls_per_paper": 2, "max_tokens": MAX_TOKENS,
                   "inputs_version": T.inputs_version(), "model": T.model(),
                   "prompts": {"abstract": "prompts/claims_abstract.system.md", "abstract_schema": "prompts/claims_abstract.schema.json", "map": "prompts/claims_map.system.md"},
                   "schema": "prompts/claims_map.schema.json", "reads": ["estimands", "estimates"], "ids": ["sentences[].id"],
                   "refs": [{"path": "sentences[].estimate_ids[]", "to": "estimates:estimates[].estimate_id"}], "fn": claims},
    }
