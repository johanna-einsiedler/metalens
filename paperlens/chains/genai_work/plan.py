"""The headline-estimate plan — the ``headline_plan`` pass, a port of the review project's
``labeling/headline_plan/trial_plan.py``. Layers: (1) the units from pass a (an outcome
category of one task) and one call for every passage describing each task; (1b) the level
each unit is analysed at and what the appendix may hold; (3b) the abstract's result sentences
split into atomic claims (one per unit, with the number if any), the introduction and the
conclusion matched to each, claims for the units the abstract does not cover taken from the
discussion, and for each claimed number the estimate that prints it or a formula over printed
cells that reproduces it, checked in code; then the rule applied per unit (the decisions the
headline rule reads). Four to five calls per paper. Reads the primary exhibits, estimands,
estimates, design and claims, the ids canonicalised on copies as the project's files were.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import math
import operator
import re
from collections import defaultdict

from . import claims as CL
from . import hashes
from . import passes as PS
from . import record as R
from . import taxonomy as T
from . import text as X

NUMFIELDS = ("n_ref", "mean_ref", "sd_ref", "n_treat", "mean_treat", "sd_treat", "n_obs", "effect_value", "se", "ci_low", "ci_high", "p_value")
CHUNK_WORDS = 90
PAGE_RE = re.compile(r"=== PAGE (\d+)")
APPX = re.compile(r"\b(online\s+)?(appendix|appendices|supplement(ary|al)?(\s+materials?)?|supporting\s+information)\b", re.I)
KEY_NAMES = {"plan_task_evidence": "task_chunks", "plan_unit_attr": "unit_attr", "plan_atomic": "atomic", "plan_discussion": "disc", "plan_chain": "chain"}
SCHEMA_OF = {"plan_task_evidence": "plan_task_evidence", "plan_unit_attr": "plan_unit_attr", "plan_atomic": "plan_atomic", "plan_discussion": "plan_atomic", "plan_chain": "plan_chain"}
MAX_TOKENS = {"plan_task_evidence": 20000, "plan_unit_attr": 16000, "plan_atomic": 16000, "plan_discussion": 16000, "plan_chain": 48000}
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg}
FUNCS = {"exp": math.exp, "log": math.log, "abs": abs}


def h8(s):
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def cache_key(prompt_name: str, user: str) -> str:
    """The project's cache key of a call: ``<name>:<h8(prompt + user)>``."""
    return f"{KEY_NAMES[prompt_name]}:{h8(hashes.prompt(prompt_name) + user)}"


def _call(ctx, prompt_name: str, user: str) -> dict:
    return ctx.call(hashes.prompt(prompt_name), user, hashes.schema(SCHEMA_OF[prompt_name]), max_tokens=MAX_TOKENS[prompt_name])


def chunks_of(body: str) -> list[dict]:
    """The paper's body in numbered chunks: runs of consecutive sentences on one page, about CHUNK_WORDS words each."""
    page, by_page = 0, {}
    for l in body.splitlines():
        m = PAGE_RE.match(l)
        if m:
            page = int(m.group(1))
            continue
        by_page.setdefault(page, []).append(l)
    kept = X.without_running_lines(by_page)
    chunks = []
    for p in sorted(kept):
        buf, n = [], 0
        for sent in X.split_sentences(X.page_text(kept[p])):
            if len(sent) < 12:
                continue
            buf.append(sent)
            n += len(sent.split())
            if n >= CHUNK_WORDS:
                chunks.append({"id": f"P{len(chunks) + 1}", "page": p, "text": " ".join(buf), "words": n})
                buf, n = [], 0
        if buf:
            chunks.append({"id": f"P{len(chunks) + 1}", "page": p, "text": " ".join(buf), "words": n})
    return chunks


def evaluate(expr, env):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in env:
                raise KeyError(n.id)
            return float(env[n.id])
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            key = f"{n.value.id}.{n.attr}"
            if key not in env:
                raise KeyError(key)
            return float(env[key])
        if isinstance(n, ast.BinOp):
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp):
            return OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS:
            return FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError(ast.dump(n))
    return ev(ast.parse(expr, mode="eval"))


def claimed_value(tok):
    xs = CL.numbers_in(tok)
    return xs[0][1] if xs else None, (xs[0][2] if xs else 0), tok.strip().endswith("%") or "percent" in tok


def headline_plan(ctx) -> dict:
    ex = ctx.read("exhibits")
    es, em, de = (copy.deepcopy(ctx.read(n)) for n in ("estimands", "estimates", "design"))
    PS.derive_findings(es); PS.derive_contrast(es); PS.derive_roles(em)
    PS.canon_ids(es, em, de)
    cl = ctx.read("claims")
    title = ctx.title or ""
    P = PS.pages_of(ctx)
    pages = P.pages
    body, _ = X.without_abstract(P.raw, (ctx.paper.get("abstract") or ""))
    lines, heads = X.scan(P.raw)
    ests = {x["estimate_id"]: x for x in em["estimates"]}
    by_est = {e["estimand_id"]: e for e in es["estimands"]}
    outs = {o["outcome_id"]: o for o in es["outcomes"]}
    tasks = {t["task_id"]: t for t in es["tasks"]}
    arms = {a["arm_id"]: a for a in es["arms"]}
    ft_label = {o["code"]: o["label"] for o in T.option_rows("finding_type")}
    kind_label = {o["code"]: o["label"] for o in T.option_rows("measure_kind")}
    category = lambda o: o.get("finding_type") or "other"  # noqa: E731
    unit_key = lambda e: (e["task_id"], category(outs.get(e["outcome_id"], {})))  # noqa: E731

    # layer 1: the units and the task evidence
    units: dict = {}
    for e in es["estimands"]:
        k = unit_key(e)
        u = units.setdefault(k, {"id": f"U{len(units) + 1}", "task": k[0], "category": k[1], "arm_pairs": [], "outcomes": [], "estimands": [], "estimates": []})
        if [e["arm_id"], e["vs_arm_id"]] not in u["arm_pairs"]:
            u["arm_pairs"].append([e["arm_id"], e["vs_arm_id"]])
        if e["outcome_id"] not in u["outcomes"]:
            u["outcomes"].append(e["outcome_id"])
        u["estimands"].append(e["estimand_id"])
        u["estimates"] += [x["estimate_id"] for x in em["estimates"] if x["estimand_id"] == e["estimand_id"]]
    unit_by_id = {u["id"]: u for u in units.values()}
    chunks = chunks_of(body)
    chunk_by_id = {ch["id"]: ch for ch in chunks}
    chunks_text = "\n\n".join(f"{ch['id']} (p. {ch['page']}): {ch['text']}" for ch in chunks)
    tev = _call(ctx, "plan_task_evidence", f"Title: {title}\n\n# The tasks\n\n{PS.tasks_arms_text(es)}\n\n# The paper, in chunks\n\n{chunks_text}")
    task_evidence: dict = {}
    for t in tev.get("tasks") or []:
        rows, seen = [], set()
        for ps in sorted(t.get("passages") or [], key=lambda x: int(re.sub(r"\D", "", x["chunk_id"]) or 0)):
            ch = chunk_by_id.get(ps["chunk_id"])
            if not ch or ch["id"] in seen:
                continue
            seen.add(ch["id"])
            rows.append({"chunk": ch["id"], "page": ch["page"], "text": ch["text"], "found": True, "informs": ps.get("informs") or [], "words": ch["words"]})
        task_evidence[t["task_id"]] = rows
    ctx.log(f"plan: body in {len(chunks)} chunks; task evidence: " + ", ".join(f"{k}: {len(v)} chunks" for k, v in task_evidence.items()))

    # layer 1b: the units' attributes, the appendix
    val = lambda x: x.get("value") if isinstance(x, dict) and "value" in x else x  # noqa: E731
    arm_attr = {}
    for t in de.get("tasks") or []:
        for a in t.get("arms") or []:
            arm_attr[a.get("arm_id")] = {k: val(a.get(k)) for k in ("ai_model", "ai_role", "is_agentic", "ai_measure", "sample_population_type", "sample_expertise", "sample_population", "n_arm")}
    study_unit = val((de.get("study") or {}).get("unit_of_analysis"))
    pointers = []
    for ch in chunks:
        for sent in X.split_sentences(ch["text"]):
            if APPX.search(sent) and X.MIN_LEN <= len(sent) <= 700:
                pointers.append({"id": f"X{len(pointers) + 1}", "page": ch["page"], "text": sent})
    ev_text = "\n\n".join(f"## task {tid}\n" + "\n".join(f"- (p. {r['page']}) {r['text']}" for r in rows[:14]) for tid, rows in task_evidence.items())
    units_text_u = "\n".join(
        f"- {u['id']}: task {u['task']} ({tasks[u['task']]['name']}), {ft_label.get(u['category'], u['category'])}; measures: "
        + "; ".join(f"{o} {outs[o]['measure']}" for o in u["outcomes"]) + "; measured on: " + ", ".join(sorted({str(outs[o].get('measured_on')) for o in u['outcomes']}))
        for u in units.values())
    user_u = (f"Title: {title}\n\n# The units\n\n{units_text_u}\n\n# The study's unit of analysis as coded: {study_unit}\n\n# The task passages\n\n{ev_text}"
              + "\n\n# Sentences pointing to an appendix or supplementary material\n\n" + ("\n".join(f"{x['id']} (p. {x['page']}): {x['text']}" for x in pointers) or "(none)"))
    ua = _call(ctx, "plan_unit_attr", user_u)
    unit_level = {x["unit_id"]: x for x in ua.get("units") or []}
    for x in unit_level.values():
        if x.get("quote"):
            x["check"] = X.check_quote(x["quote"], x.get("page") or 0, pages)
    ptr_units = {x["pointer_id"]: x for x in ua.get("pointers") or []}
    for x in pointers:
        x.update({"unit_ids": [u for u in (ptr_units.get(x["id"]) or {}).get("unit_ids") or [] if u in unit_by_id], "may_hold": (ptr_units.get(x["id"]) or {}).get("may_hold") or ""})
    refs_line = next((i for i, k, _ in heads if k == "references"), None)
    appendix = {"in_pdf": any(k == "appendix" and refs_line is not None and i > refs_line and CL.strong(h) and re.search(r"appendix|supplement", h, re.I) for i, k, h in heads)
                or any(re.match(r"^(Table|Figure|Fig\.?)\s*[A-Z]\d", x.get("id") or "") for x in (ex.get("tables") or [])),
                "referenced": bool(pointers), "pointers": pointers}
    for u in units.values():
        lv = unit_level.get(u["id"]) or {}
        treat = sorted({a for a, b in u["arm_pairs"]} | {b for a, b in u["arm_pairs"]})
        u["attributes"] = {"level": lv.get("level"), "unit_size": lv.get("unit_size"), "level_quote": lv.get("quote"), "level_page": lv.get("page"), "level_check": lv.get("check"),
                           "study_unit_of_analysis": study_unit,
                           "measured_on": sorted({str(outs[o].get("measured_on")) for o in u["outcomes"]}),
                           "estimand_types": sorted({str(by_est[eid].get("estimand_type")) for eid in u["estimands"]}),
                           "contrast_types": sorted({str(by_est[eid].get("contrast_type")) for eid in u["estimands"]}),
                           "cluster_levels": sorted({str(ests[x].get("cluster_level")) for x in u["estimates"] if ests[x].get("cluster_level")}),
                           "arms": [{"arm_id": a, "label": arms[a]["label"], "is_reference": bool(arms[a].get("is_reference")), **(arm_attr.get(a) or {})} for a in treat]}

    # layer 3b: atomic claims, the conclusion, the numbers
    disc_i = next((i for i, k, s in heads if k == "discussion"), None)
    refs_i = next((i for i, k, s in heads if k == "references" and disc_i is not None and i > disc_i), len(lines))
    page_no, by_page = 0, {}
    for i, l in enumerate(lines):
        m = PAGE_RE.match(l)
        if m:
            page_no = int(m.group(1))
            continue
        if disc_i is not None and disc_i <= i < refs_i:
            by_page.setdefault(page_no, []).append(l)
    concl = []
    for p in sorted(by_page):
        for s in X.split_sentences(X.page_text(by_page[p])):
            if X.MIN_LEN <= len(s) <= 900:
                concl.append({"id": f"D{len(concl) + 1}", "page": p, "text": s})
    intro_sents = cl.get("intro_sentences") or []
    intro = {s["id"]: s for s in intro_sents}
    abstract_results = [s for s in cl.get("sentences") or [] if s.get("kind") == "result"]
    units_text = "\n".join(
        f"- {u['id']}: task {u['task']} ({tasks[u['task']]['name']}), {ft_label.get(u['category'], u['category'])}; arms compared: "
        + ", ".join(f"{a} ({arms[a]['label']}) vs {b} ({arms[b]['label']})" for a, b in u["arm_pairs"]) + "; "
        + "measures: " + "; ".join(f"{o} {outs[o]['measure']}" + (f" [{kind_label.get(outs[o].get('measure_kind'), outs[o].get('measure_kind'))}]" if outs[o].get("measure_kind") else "") for o in u["outcomes"])
        for u in units.values())
    user2 = (f"Title: {title}\n\n# The abstract's result sentences\n\n" + "\n".join(f"{s['id']}: {s['text']}" for s in abstract_results)
             + f"\n\n# The units\n\n{units_text}\n\n# The introduction\n\n" + "\n".join(f"{s['id']} (p. {s['page']}): {s['text']}" for s in intro_sents)
             + "\n\n# The discussion / conclusion\n\n" + "\n".join(f"{s['id']} (p. {s['page']}): {s['text']}" for s in concl))
    atomic = _call(ctx, "plan_atomic", user2)["claims"]
    for a in atomic:
        a["source"] = "abstract"
    covered = {a.get("unit_id") for a in atomic}
    uncovered = [u for u in units.values() if u["id"] not in covered]
    disc_claims = []
    if uncovered and concl:
        utext = "\n".join(
            f"- {u['id']}: task {u['task']} ({tasks[u['task']]['name']}), {ft_label.get(u['category'], u['category'])}; measures: "
            + "; ".join(f"{o} {outs[o]['measure']}" for o in u["outcomes"]) for u in uncovered)
        userd = (f"Title: {title}\n\n# The discussion / conclusion\n\n" + "\n".join(f"{s['id']} (p. {s['page']}): {s['text']}" for s in concl)
                 + f"\n\n# The units the abstract does not cover\n\n{utext}\n\n# The introduction\n\n" + "\n".join(f"{s['id']} (p. {s['page']}): {s['text']}" for s in intro_sents))
        disc_claims = _call(ctx, "plan_discussion", userd)["claims"]
        for a in disc_claims:
            a["source"] = "discussion"
            a["id"] = a["id"] if a["id"].startswith("d") else "d" + a["id"].lstrip("cC")
        disc_claims = [a for a in disc_claims if a.get("unit_id") in {u["id"] for u in uncovered}]
        atomic = atomic + disc_claims
    for a in atomic:
        a["intro"] = [intro[i] for i in a.get("intro_ids") or [] if i in intro]
        a["conclusion"] = [next(d for d in concl if d["id"] == i) for i in a.get("conclusion_ids") or [] if any(d["id"] == i for d in concl)]
        a["numbers_in_restatements"] = sorted({tok for s in a["intro"] + a["conclusion"] for tok, v, d in CL.numbers_in(s["text"])})
    ctx.log(f"plan: {len(atomic) - len(disc_claims)} atomic claims from {len(abstract_results)} abstract sentences, {len(disc_claims)} from the discussion for {len(uncovered)} uncovered units")

    # the number behind each claim: printed, or a formula over printed cells
    def est_text(x):
        nums = ", ".join(f"{k} = {x[k]}" for k in NUMFIELDS if x.get(k) is not None)
        extra = ", ".join(f"{k} = {x[k]}" for k in ("effect_reported", "effect_type", "effect_units", "p_reported") if x.get(k))
        return f"- estimate `{x['estimate_id']}` (estimand {x['estimand_id']}, role {x.get('role')}, {R._source(x.get('source'))}, p. {(x.get('source') or {}).get('page')}): {x.get('specification')}; {nums}" + (f"; {extra}" if extra else "")
    numeric_claims = []
    for a in atomic:
        if not a.get("unit_id") or a["unit_id"] not in unit_by_id:
            continue
        if a.get("number"):
            numeric_claims.append({"claim_id": a["id"], "number": a["number"], "unit_id": a["unit_id"], "from": a.get("source", "abstract"), "statement": a["statement"]})
        else:
            for s in a["intro"] + a["conclusion"]:
                for tok, v, d in CL.numbers_in(s["text"]):
                    numeric_claims.append({"claim_id": f"{a['id']}/{s['id']}", "number": tok, "unit_id": a["unit_id"], "from": s["id"], "statement": s["text"]})
    chains = []
    if numeric_claims:
        parts = []
        for q in numeric_claims:
            u = unit_by_id[q["unit_id"]]
            parts.append(f"## claim {q['claim_id']} — number {q['number']} — unit {u['id']} ({ft_label.get(u['category'], u['category'])}, task {u['task']})\n{q['statement']}\n"
                         + "\n".join(f"### estimand {eid}: {by_est[eid]['statement']} (subgroup {by_est[eid].get('subgroup')}; horizon {by_est[eid].get('horizon')})\n"
                                     + "\n".join(est_text(ests[x]) for x in u["estimates"] if ests[x]["estimand_id"] == eid) for eid in u["estimands"]))
        batches, batch, size = [], [], 0
        for part in parts:
            if batch and size + len(part) > 1_500_000:
                batches.append(batch)
                batch, size = [], 0
            batch.append(part)
            size += len(part)
        batches.append(batch)
        for b in batches:
            user3 = f"Title: {title}\n\n" + "\n\n".join(b)
            chains += _call(ctx, "plan_chain", user3)["chains"]
    for ch in chains:
        q = next((q for q in numeric_claims if q["claim_id"] == ch["claim_id"]), None)
        ch["unit_id"] = q["unit_id"] if q else None
        v, dec, is_pct = claimed_value(ch["number"])
        ch["claimed_value"] = v
        ch["verified"], ch["computed"], ch["cells"] = None, None, []
        if ch.get("printed_in"):
            eid, _, field = ch["printed_in"].partition(".")
            x = ests.get(eid) or {}
            value = x.get(field)
            try:
                value = abs(float(value)) if value is not None else None
            except (TypeError, ValueError):
                value = None
            ch["computed"] = value
            ch["verified"] = value is not None and v is not None and (abs(value - v) < 1e-9 or abs(round(value, dec) - v) < 1e-9 or abs(round(value * 100, dec) - v) < 1e-9)
            if x:
                ch["cells"].append({"name": field, "estimate_id": eid, "field": field, "value": x.get(field), "source": R._source(x.get("source")), "page": (x.get("source") or {}).get("page")})
        elif ch.get("formula"):
            env = {}
            for inp in ch.get("inputs") or []:
                x = ests.get(inp["estimate_id"]) or {}
                value = x.get(inp["field"])
                ch["cells"].append({"name": inp["name"], "estimate_id": inp["estimate_id"], "field": inp["field"], "value": value, "source": R._source(x.get("source")), "page": (x.get("source") or {}).get("page")})
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    env[inp["name"]] = value
            try:
                out = evaluate(ch["formula"], env)
                ch["computed"] = out
                cand = [abs(out), abs(out) * 100] if is_pct or v is not None and v > 1 else [abs(out)]
                ch["verified"] = v is not None and any(abs(round(x_, dec) - v) < 1e-9 or abs(x_ - v) < 1e-9 for x_ in cand)
                bound = q and re.search(r"\b(over|more than|above|at least|exceed\w*)\s+" + re.escape(ch["number"].split()[0]), q["statement"], re.I)
                if not ch["verified"] and bound and any(v <= x_ <= v * 1.1 for x_ in cand):
                    ch["verified"], ch["bound"] = True, "a stated lower bound; the computed value lies within 10% above it"
            except Exception as e:  # noqa: BLE001
                ch["computed"], ch["verified"], ch["error"] = None, False, str(e)[:120]

    # the rule, per unit
    decisions = []
    for u in units.values():
        cs = [a for a in atomic if a.get("unit_id") == u["id"] and a.get("source") == "abstract"]
        prefix = ""
        if not cs:
            cs = [a for a in atomic if a.get("unit_id") == u["id"] and a.get("source") == "discussion"]
            prefix = "no abstract claim → the discussion's: " if cs else ""
        numeric = [a for a in cs if a.get("number")]
        chs = [ch for ch in chains if ch.get("unit_id") == u["id"]]
        mains = [x for x in u["estimates"] if ests[x].get("role") == "main"]
        d = {"unit": u["id"], "claims": [a["id"] for a in cs], "numeric_claims": [a["id"] for a in numeric], "branch": None, "main": None, "chain": None, "note": ""}

        def pick(ch_list):
            good = [ch for ch in ch_list if ch["verified"]] or ch_list
            return good[0] if good else None

        def placed(ch_list, u=u):
            return [ch for ch in ch_list if (ch.get("printed_in") or ch.get("formula")) and ch.get("estimate_ids") and set(ch["estimate_ids"]) <= set(u["estimates"])]

        def in_appendix(eid):
            exh = ((ests.get(eid) or {}).get("source") or {}).get("exhibit") or ""
            return bool(re.search(r"\b(appendix|[A-Z]\d|supplement)", exh, re.I))

        if not cs:
            d["branch"], d["main"], d["note"] = "no claim in the abstract or the discussion → results lead", mains, "no headline claim anywhere; the specification the results section leads with"
        elif len(cs) == 1:
            a = cs[0]
            if a.get("number"):
                ch = pick([ch for ch in chs if ch["claim_id"] == a["id"]])
                d["branch"] = "one claim, numeric → " + ("printed" if ch and ch.get("printed_in") else "derived by formula" if ch and ch.get("formula") else "no chain found")
                d["main"], d["chain"] = (ch["estimate_ids"] if ch else []), (ch["claim_id"] if ch else None)
            else:
                sub = placed([ch for ch in chs if ch["claim_id"].startswith(a["id"] + "/")])
                nums = {ch["number"] for ch in sub}
                if len(nums) == 1:
                    ch = pick(sub)
                    d["branch"], d["main"], d["chain"] = "one claim, not numeric → intro/conclusion give one number", ch["estimate_ids"], ch["claim_id"]
                elif not sub:
                    d["branch"], d["main"], d["note"] = "one claim, not numeric → no number about it anywhere → results lead", mains, "the restatements carry no number about this unit"
                else:
                    d["branch"], d["main"], d["note"] = "one claim, not numeric → several numbers in the restatements → results lead", mains, "numbers: " + ", ".join(sorted(nums))
        else:
            if len(numeric) == 1:
                ch = pick([ch for ch in chs if ch["claim_id"] == numeric[0]["id"]])
                d["branch"] = "several claims, one numeric → prefer it → " + ("printed" if ch and ch.get("printed_in") else "derived" if ch and ch.get("formula") else "no chain")
                d["main"], d["chain"] = (ch["estimate_ids"] if ch else []), (ch["claim_id"] if ch else None)
            elif len(numeric) > 1:
                counts: dict = defaultdict(int)
                for ch in chs:
                    if ch["claim_id"] in {a["id"] for a in numeric}:
                        for x in ch["estimate_ids"]:
                            counts[x] += 1 + sum(len(a["intro"]) + len(a["conclusion"]) for a in numeric if a["id"] == ch["claim_id"])
                best = sorted(counts.items(), key=lambda kv: (-kv[1], in_appendix(kv[0]), kv[0]))
                tie = len(best) > 1 and best[0][1] == best[1][1]
                d["branch"] = "several numeric claims → most mentioned" + (" → tie → tie-break: main-text table first, then the first estimate id" if tie else "")
                d["main"] = [best[0][0]] if best else mains
                d["note"] = "mentions: " + ", ".join(f"{k} ×{n}" for k, n in best)
            else:
                sub = placed([ch for ch in chs if "/" in ch["claim_id"]])
                nums = {ch["number"] for ch in sub}
                if len(nums) == 1:
                    ch = pick(sub)
                    d["branch"], d["main"], d["chain"] = "several claims, none numeric → one number in the restatements", ch["estimate_ids"], ch["claim_id"]
                else:
                    d["branch"], d["main"], d["note"] = "several claims, none numeric → " + ("no" if not nums else "several") + " numbers → results lead", mains, ", ".join(sorted(nums))
        if prefix and d["branch"]:
            d["branch"] = prefix + d["branch"]
        agg = [o for o in u["outcomes"] if re.search(r"composite|averag|sum of|aggregat|index|combined", (outs[o].get("how_measured") or "") + " " + (outs[o].get("measure") or ""), re.I)]
        if agg:
            d["aggregate"] = [f"{o}: {outs[o]['measure']} — {outs[o]['how_measured']}" for o in agg]
        decisions.append(d)
    ctx.log("plan: " + "; ".join(f"{d['unit']}: {d['branch']}" for d in decisions))
    return {"units": list(units.values()), "task_evidence": task_evidence, "appendix": appendix, "atomic_claims": atomic, "conclusion_sentences": concl,
            "uncovered_units": [u["id"] for u in uncovered], "numeric_claims": numeric_claims, "chains": chains, "decisions": decisions, "n_chunks": len(chunks)}


def registrations() -> dict[str, dict]:
    return {
        "headline_plan": {"kind": "pass", "label": "The headline-estimate plan from the abstract's claims", "calls_per_paper": 4, "max_tokens": 48_000,
                          "inputs_version": T.inputs_version(), "model": T.model(),
                          "prompts": {"task_evidence": "prompts/plan_task_evidence.system.md", "task_evidence_schema": "prompts/plan_task_evidence.schema.json",
                                      "unit_attr": "prompts/plan_unit_attr.system.md", "unit_attr_schema": "prompts/plan_unit_attr.schema.json",
                                      "atomic": "prompts/plan_atomic.system.md", "atomic_schema": "prompts/plan_atomic.schema.json",
                                      "discussion": "prompts/plan_discussion.system.md", "chain": "prompts/plan_chain.system.md"},
                          "schema": "prompts/plan_chain.schema.json", "reads": ["exhibits", "estimands", "estimates", "design", "claims"],
                          "ids": ["units[].id", "atomic_claims[].id"],
                          "refs": [{"path": "decisions[].main[]", "to": "estimates:estimates[].estimate_id"}], "fn": headline_plan},
    }
