"""The four extraction passes of the GenAI-at-work chain as registered pass code — a port of the
review project's ``scripts/extraction/split.py`` (its ``run``): b the exhibits as printed, a the
estimands (tasks, arms, outcomes, what the paper estimates), c the estimates read into the
estimands from the exhibits, d the study, task and arm variables. Each pass's prompt and
schema are the files of ``prompts/`` (its pin); the user text, the retries on a cut answer, the
chunking of a long estimates request and the post-processing (quote checks, derived cells) are
the project's, line for line, over the engine's ``ctx``. The stored output of a pass equals the
file the project writes under ``labeling/split/<rid>/``.
"""
from __future__ import annotations

import re

from paperlens.passes import Truncated

from . import hashes
from . import taxonomy as T
from . import text as X

MAX_OUT = 60_000
PARTS_ABOVE = 100   # estimands: a longer list is asked for in parts (the answer for more does not fit the output cap)
PART = 40           # estimands per part; the whole list is shown each time, the part's estimands marked
# the contrast a row against the reference arm may carry, by what the reference arm is (checks.CONTRAST_FOR)
CONTRAST_FOR = {"no_ai_concurrent": {"ai_vs_no_ai"}, "no_ai_prior": {"pre_vs_post", "ai_vs_no_ai"}, "no_ai_after": {"pre_vs_post", "ai_vs_no_ai"},
                "placebo_tool": {"ai_vs_no_ai"}, "other_ai": {"ai_vs_ai", "ai_variant", "dose"},
                "expert_benchmark": {"vs_benchmark"}, "external_norm": {"vs_benchmark"}, "ai_alone": {"human_ai_vs_ai"}}
PREFERRED_CONTRAST = ("ai_vs_no_ai", "pre_vs_post", "ai_vs_ai", "ai_variant", "dose", "vs_benchmark", "human_ai_vs_ai")


# ── the model call, as the project's cached_call made it ─────────────────────────────

def call(ctx, name: str, user: str, *, short: bool = True, max_tokens: int = MAX_OUT, tries_bad: int = 2) -> dict:
    """One answer for the pass ``name``: a cut answer is asked again once with the pass's short
    form appended (when the pass has one), a bad answer twice. ``RuntimeError('truncated')`` when
    the short form is not allowed — the estimates pass then halves its request."""
    system, schema = hashes.prompt(name), hashes.schema(name)
    err = "bad answer"
    for _ in range(tries_bad):
        try:
            return ctx.call(system, user, schema, max_tokens=max_tokens)
        except Truncated:
            if not short:
                raise RuntimeError("truncated")
            try:
                ans = ctx.call(system, user + hashes.short(name), schema, max_tokens=max_tokens)
            except Truncated:
                raise RuntimeError("truncated")
            ctx.log(f"{name}: answered in the short form")
            ctx.short = True
            return ans
        except ValueError as exc:                      # not a JSON object: once more
            err = f"bad_json:{str(exc)[:80]}"
            continue
    raise RuntimeError(f"bad answer ({err})")


def pages_of(ctx) -> X.Pages:
    return X.Pages(ctx.pages)


def body_of(ctx, P: X.Pages) -> tuple[str, bool]:
    """The text the passes read: the page-marked text with the abstract dropped (the paper's
    abstract when the engine knows it, else the 'Abstract' block)."""
    return X.without_abstract(P.raw, (ctx.paper.get("abstract") or ""))


# ── pass b: the exhibits ──────────────────────────────────────────────

def verify_exhibits(ex: dict, pages: dict, rpages: dict) -> dict:
    for tb in ex.get("tables") or []:
        pg = int(tb.get("page") or 0)
        cells = [c for p in tb.get("panels") or [] for row in p.get("rows") or [] for c in row]
        nums = [c for c in cells if re.search(r"\d", str(c))][:60]
        found = sum(1 for c in nums if X.table_cells(str(c), pg, pages) is not None)
        tb["cells_checked"], tb["cells_found"] = len(nums), found
        tb["check"] = "table_cells" if nums and found >= 0.7 * len(nums) else ("empty" if not nums else "not_found")
        for p in tb.get("panels") or []:
            p["markdown"] = X.table_text({"caption": p.get("panel") or "", "header": p.get("header") or [], "rows": p.get("rows") or []})
    for fg in ex.get("figures") or []:
        pg = int(fg.get("page") or 0)
        nums = [str(n.get("value")) for n in fg.get("numbers") or [] if re.search(r"\d", str(n.get("value") or ""))][:40]
        fg["numbers_checked"] = len(nums)
        fg["numbers_found"] = sum(1 for v in nums if X.table_cells(v, pg, pages) is not None)
    for tn in ex.get("text_numbers") or []:
        check_passage(tn, pages, rpages)
    return ex


def check_passage(obj: dict, pages: dict, rpages: dict, qk: str = "quote", pk: str = "page", ck: str = "check") -> dict:
    """The quote check of the screening judge on one object: found on its page, snapped to the
    paper's wording, found as table cells, or not found."""
    q, pg = obj.get(qk) or "", int(obj.get(pk) or 0)
    if not q:
        obj[ck] = "empty"
        return obj
    st = X.check_quote(q, pg, pages)
    if st == "not_found":
        hit = X.snap_quote(q, pg, rpages)
        if hit:
            obj["model_" + qk], obj[qk], obj[pk], st = q, hit[0], hit[1], "snapped"
        else:
            tp = X.table_cells(q, pg, pages)
            if tp is not None:
                obj[pk], st = tp, "table_cells"
    obj[ck] = st
    return obj


def exhibit_headers(ex: dict) -> str:
    out = []
    for tb in ex.get("tables") or []:
        out.append(f"- {tb.get('id')} (p. {tb.get('page')}): {tb.get('caption')}")
        for p in tb.get("panels") or []:
            head = " | ".join(p.get("header") or [])
            rows = "; ".join(str((r or [''])[0]) for r in (p.get("rows") or [])[:40])
            out.append(f"    {'panel ' + p['panel'] + ': ' if p.get('panel') else ''}columns: {head}\n    rows: {rows}")
    for fg in ex.get("figures") or []:
        out.append(f"- {fg.get('id')} (p. {fg.get('page')}): {fg.get('caption')} — {fg.get('note')}")
    return "\n".join(out) or "(no tables or figures)"


def exhibits_text(ex: dict) -> str:
    out = []
    for tb in ex.get("tables") or []:
        out.append(f"## {tb.get('id')} (p. {tb.get('page')}) — {tb.get('caption')}")
        for p in tb.get("panels") or []:
            out.append(p.get("markdown") or "")
        if tb.get("notes"):
            out.append(f"Notes: {tb['notes']}")
    for fg in ex.get("figures") or []:
        out.append(f"## {fg.get('id')} (p. {fg.get('page')}) — {fg.get('caption')}\n{fg.get('note')}")
        for n in fg.get("numbers") or []:
            out.append(f"- {n.get('label')}: {n.get('value')}")
    out.append("## Numbers stated in the text")
    for tn in ex.get("text_numbers") or []:
        out.append(f"- (p. {tn.get('page')}) “{tn.get('quote')}” — {tn.get('about')}")
    return "\n\n".join(out)


def exhibits(ctx) -> dict:
    P = pages_of(ctx)
    body, dropped = body_of(ctx, P)
    ctx.log("abstract withheld" if dropped else "no abstract block found: the model sees it")
    ex = call(ctx, "exhibits", f"Title: {ctx.title}\n\n{body}")
    return verify_exhibits(ex, P.pages, P.rpages)


# ── pass a: the estimands ─────────────────────────────────────────────

def derive_findings(es: dict) -> dict:
    """A performance outcome's finding type follows from its measure kind (taxonomy.KIND_FINDING);
    the model's own answer is kept in finding_type_model when it differed. A kind on an outcome
    of another class is dropped."""
    kf = T.kind_finding()
    for o in es.get("outcomes") or []:
        if o.get("outcome_class") != "performance":
            o["measure_kind"] = None
            continue
        f = kf.get(o.get("measure_kind"))
        if f and o.get("finding_type") != f:
            o["finding_type_model"], o["finding_type"] = o.get("finding_type"), f
    return es


def derive_contrast(es: dict) -> dict:
    """An estimand's contrast type follows from what its reference arm is (CONTRAST_FOR); the
    model's answer is kept in contrast_type_model when it differed. A row-own contrast
    (interaction, association) stands as the model said, and so does an estimand whose reference
    arm has no control type."""
    ref = {a["arm_id"]: a.get("control_type") for a in es.get("arms") or [] if a.get("is_reference")}
    for e in es.get("estimands") or []:
        allowed = CONTRAST_FOR.get(ref.get(e.get("vs_arm_id")))
        ct = e.get("contrast_type")
        if not allowed or ct in ("interaction", "association") or ct in allowed:
            continue
        e["contrast_type_model"], e["contrast_type"] = ct, next(c for c in PREFERRED_CONTRAST if c in allowed)
    return es


def verify_estimands(es: dict, pages: dict, rpages: dict) -> dict:
    for key in ("tasks", "arms", "outcomes", "estimands"):
        for o in es.get(key) or []:
            check_passage(o, pages, rpages)
            if key == "arms" and o.get("n_quote"):
                check_passage(o, pages, rpages, "n_quote", "n_page", "n_check")
    return es


def estimands_text(es: dict, active=None) -> str:
    """The pass-a output as pass c reads it. With ``active`` (estimand ids), each estimand line is
    marked ► when this request answers it and · when another request does."""
    out = ["## Tasks"]
    for t in es.get("tasks") or []:
        out.append(f"- {t['task_id']}: {t['name']} — {t['description']} (sample: {t['sample']})")
    out.append("## Arms")
    for a in es.get("arms") or []:
        out.append(f"- {a['arm_id']} (tasks {', '.join(a.get('task_ids') or [])}): {a['label']}; "
                   f"{'REFERENCE (' + str(a.get('control_type')) + '); ' if a.get('is_reference') else ''}got: {a['treatment']}; sample: {a['sample']}; n = {a.get('n')}")
    out.append("## Outcomes")
    for o in es.get("outcomes") or []:
        out.append(f"- {o['outcome_id']}: {o['measure']} [{o.get('unit')}], class = {o.get('outcome_class')}, better = {o.get('direction')}; {o['how_measured']}")
    out.append("## Estimands")
    for e in es.get("estimands") or []:
        mark = "" if active is None else ("► " if e["estimand_id"] in active else "· ")
        out.append(f"- {mark}{e['estimand_id']}: task {e['task_id']}, {e['arm_id']} vs {e['vs_arm_id']}, outcome {e['outcome_id']}, "
                   f"subgroup: {e['subgroup']}, horizon: {e.get('horizon')}, contrast: {e.get('contrast_type')}, exhibits: {', '.join(e.get('exhibits') or []) or '—'}. {e['statement']}")
    return "\n".join(out)


def tasks_arms_text(es: dict) -> str:
    out = []
    for t in es.get("tasks") or []:
        out.append(f"- task {t['task_id']}: {t['name']} — {t['description']} (sample: {t['sample']})")
        for a in es.get("arms") or []:
            if t["task_id"] in (a.get("task_ids") or []):
                out.append(f"    - arm {a['arm_id']}: {a['label']}{' (the reference arm; control_type ' + str(a.get('control_type')) + ', given: copy it)' if a.get('is_reference') else ''}; got: {a['treatment']}; sample: {a['sample']}; n = {a.get('n')}")
    return "\n".join(out)


def estimands(ctx) -> dict:
    P = pages_of(ctx)
    body, _dropped = body_of(ctx, P)
    ex = ctx.read("exhibits")
    es = call(ctx, "estimands", f"Title: {ctx.title}\n\n# The tables and figures (headers and row labels only)\n\n{exhibit_headers(ex)}\n\n# The paper\n\n{body}")
    verify_estimands(es, P.pages, P.rpages)
    derive_findings(es)
    derive_contrast(es)
    return es


# ── pass c: the estimates ─────────────────────────────────────────────

def verify_estimates(em: dict, pages: dict, rpages: dict) -> dict:
    for e in em.get("estimates") or []:
        check_passage(e, pages, rpages)
        if e.get("text_quote"):
            check_passage(e, pages, rpages, "text_quote", "text_page", "text_check")
    return em


def derive_roles(em: dict) -> int:
    """An estimand's only estimate is the one the authors lead with: role main, the model's answer
    kept as role_model (idempotent)."""
    by: dict = {}
    for e in em.get("estimates") or []:
        by.setdefault(e.get("estimand_id"), []).append(e)
    n = 0
    for _eid, ests in by.items():
        if len(ests) == 1 and ests[0].get("role") in ("supporting", "robustness"):
            e = ests[0]
            e.setdefault("role_model", e["role"]); e["role"] = "main"; n += 1
    return n


def estimates_user(title, es: dict, ex: dict, text: str, active=None, note=None) -> str:
    head = f"# This request\n\n{note}\n\n" if note else ""
    return (f"{head}Title: {title}\n\n# Pass a: what the paper estimates\n\n{estimands_text(es, active)}\n\n# Pass b: the exhibits as printed\n\n{exhibits_text(ex)}"
            f"\n\n# The paper (for the role of each estimate only)\n\n{text}")


def part_note(n_all: int, n_active: int, orphans: bool) -> str:
    return (f"The paper's {n_all} estimands are listed in full below. This request answers the {n_active} marked ►; the others (·) are "
            "answered in other requests, so give them no estimate and no unmatched entry. Every ► estimand appears once, in `estimates` "
            "or in `unmatched_estimands`. "
            + ("List `orphan_numbers` as usual: numbers that belong to none of the listed estimands, marked or not."
               if orphans else "Leave `orphan_numbers` empty in this request; another request lists them."))


def fill_note(n_all: int, n_active: int) -> str:
    return (f"A completion. The paper's {n_all} estimands are listed in full below; the {n_active} marked ► were not answered by the "
            "earlier request(s). Answer them now, each once, in `estimates` or in `unmatched_estimands`; give the others (·) nothing "
            "and leave `orphan_numbers` empty.")


def merge_estimates(answers, ids) -> dict:
    """The parts' answers as one: each part's estimates and unmatched entries for its own estimands
    (an estimate naming an estimand pass a does not have is kept for the check), orphan numbers
    once per cell, estimate ids made unique."""
    known = set(ids)
    em: dict = {"estimates": [], "unmatched_estimands": [], "orphan_numbers": []}
    seen_orphan, seen_id = set(), {}
    for part_ids, ans in answers:
        allowed = set(part_ids)
        for e in ans.get("estimates") or []:
            if e.get("estimand_id") in allowed or e.get("estimand_id") not in known:
                em["estimates"].append(e)
        for u in ans.get("unmatched_estimands") or []:
            if u.get("estimand_id") in allowed:
                em["unmatched_estimands"].append(u)
        for o in ans.get("orphan_numbers") or []:
            k = (o.get("exhibit"), o.get("where"))
            if k not in seen_orphan:
                seen_orphan.add(k); em["orphan_numbers"].append(o)
    for e in em["estimates"]:
        eid = e.get("estimate_id")
        if eid in seen_id:
            seen_id[eid] += 1; e["estimate_id"] = f"{eid}.{seen_id[eid]}"
        else:
            seen_id[eid] = 1
    return em


def covered(em: dict) -> set:
    return {e.get("estimand_id") for e in em.get("estimates") or []} | {u.get("estimand_id") for u in em.get("unmatched_estimands") or []}


def run_estimates(ctx, title, es: dict, ex: dict, text: str) -> dict:
    """Pass c. One request when the paper has at most PARTS_ABOVE estimands; above that, parts of
    PART estimands, the whole list shown each time with the part's estimands marked and the
    orphan numbers asked in the first part only. A request whose answer does not fit is asked
    again in two halves (the short form only for a handful of estimands). Estimands no answer
    mentions are asked for once more (a completion); those still missing are written as
    unmatched, "not answered", so that nothing is dropped silently. Returns the merged answer
    with ``parts`` listing the requests."""
    ids = [e["estimand_id"] for e in es.get("estimands") or []]
    parts: list[dict] = []

    def ask(label, active, note, orphans):
        n_active = len(active) if active is not None else len(ids)
        allow_short = n_active <= 8
        ctx.short = False
        try:
            ans = call(ctx, "estimates", estimates_user(title, es, ex, text, active, note), short=allow_short)
        except RuntimeError as e:
            if "truncated" not in str(e) or n_active <= 8:
                raise
            act = list(active) if active is not None else list(ids)
            half = len(act) // 2
            ctx.log(f"c estimates: the answer for {n_active} estimands did not fit; asked in two halves")
            return (ask(f"{label}a", act[:half], part_note(len(ids), half, orphans), orphans)
                    + ask(f"{label}b", act[half:], part_note(len(ids), len(act) - half, False), False))
        parts.append({"key": label, "estimands": n_active, "short": bool(getattr(ctx, "short", False))})
        return [(list(active) if active is not None else list(ids), ans)]

    if len(ids) <= PARTS_ABOVE:
        answers = ask("estimates:1", None, None, True)
    else:
        n = -(-len(ids) // PART)
        answers = []
        for i in range(n):
            chunk = ids[i * PART:(i + 1) * PART]
            answers += ask(f"estimates:1:part={i + 1}/{n}", chunk, part_note(len(ids), len(chunk), i == 0), i == 0)
    em = merge_estimates(answers, ids)
    missing = [i for i in ids if i not in covered(em)]
    if missing:
        n = -(-len(missing) // PART)
        ctx.log(f"c estimates: {len(missing)} estimands not answered; asked once more in {n} request(s)")
        for i in range(n):
            chunk = missing[i * PART:(i + 1) * PART]
            answers += ask(f"estimates:1:fill={i + 1}/{n}", chunk, fill_note(len(ids), len(chunk)), False)
        em = merge_estimates(answers, ids)
        for i in [i for i in ids if i not in covered(em)]:
            em["unmatched_estimands"].append({"estimand_id": i, "reason": "other", "why": "not answered by the model (asked twice)"})
    em["parts"] = parts
    return em


def estimates(ctx) -> dict:
    P = pages_of(ctx)
    body, _dropped = body_of(ctx, P)
    ex, es = ctx.read("exhibits"), ctx.read("estimands")
    em = run_estimates(ctx, ctx.title, es, ex, body)
    return verify_estimates(em, P.pages, P.rpages)


# ── pass d: the design ────────────────────────────────────────────────

def design(ctx) -> dict:
    P = pages_of(ctx)
    body, _dropped = body_of(ctx, P)
    es = ctx.read("estimands")
    return call(ctx, "design", f"Title: {ctx.title}\n\n# The tasks and arms (from pass a; ids fixed)\n\n{tasks_arms_text(es)}\n\n# The paper\n\n{body}", short=False)


def schema_nodes(obj) -> int:
    """A count of the nodes a JSON schema has, against the provider's limit (the project guards
    the design schema at 880)."""
    if isinstance(obj, dict):
        return 1 + sum(schema_nodes(v) for v in obj.values())
    if isinstance(obj, list):
        return 1 + sum(schema_nodes(v) for v in obj)
    return 1


def registrations() -> dict[str, dict]:
    iv = T.inputs_version(); model = T.model()
    base = {"kind": "pass", "k_ok": True, "max_tokens": MAX_OUT, "inputs_version": iv, "model": model, "calls_per_paper": 1}
    return {
        "exhibits": {**base, "label": "Exhibits as printed (pass b)", "prompts": {"system": "prompts/exhibits.system.md", "short": "prompts/exhibits.short.md"},
                     "schema": "prompts/exhibits.schema.json", "ids": ["tables[].id", "figures[].id"], "fn": exhibits},
        "estimands": {**base, "label": "Estimands: tasks, arms, outcomes (pass a)", "prompts": {"system": "prompts/estimands.system.md", "short": "prompts/estimands.short.md"},
                      "schema": "prompts/estimands.schema.json", "reads": ["exhibits"],
                      "ids": ["tasks[].task_id", "arms[].arm_id", "outcomes[].outcome_id", "estimands[].estimand_id"],
                      "refs": [{"path": "arms[].task_ids[]", "to": "tasks[].task_id"}, {"path": "estimands[].task_id", "to": "tasks[].task_id"},
                               {"path": "estimands[].arm_id", "to": "arms[].arm_id"}, {"path": "estimands[].vs_arm_id", "to": "arms[].arm_id"},
                               {"path": "estimands[].outcome_id", "to": "outcomes[].outcome_id"}],
                      "fn": estimands},
        "estimates": {**base, "label": "Estimates read into the estimands (pass c)", "prompts": {"system": "prompts/estimates.system.md", "short": "prompts/estimates.short.md"},
                      "schema": "prompts/estimates.schema.json", "reads": ["exhibits", "estimands"], "ids": ["estimates[].estimate_id"],
                      "refs": [{"path": "estimates[].estimand_id", "to": "estimands:estimands[].estimand_id"},
                               {"path": "unmatched_estimands[].estimand_id", "to": "estimands:estimands[].estimand_id"}],
                      "fn": estimates},
        "design": {**base, "label": "Study, task and arm variables (pass d)", "prompts": {"system": "prompts/design.system.md"},
                   "schema": "prompts/design.schema.json", "reads": ["estimands"],
                   "refs": [{"path": "tasks[].task_id", "to": "estimands:tasks[].task_id"}, {"path": "tasks[].arms[].arm_id", "to": "estimands:arms[].arm_id"}],
                   "fn": design},
    }


# ── canonical ids (the finish step's first move; applied by the assemble compute) ─────────

def canon_ids(es: dict, em: dict, de: dict) -> tuple[dict, dict, dict]:
    """The ids the codebook uses, whatever the model wrote: tasks T1, T2 … in order; arms C for
    the reference arm (C2, C3 … for a second and third reference arm), then T1, T2 … in order;
    applied to the estimands, the estimates and the design pass alike."""
    tmap = {t["task_id"]: f"T{i}" for i, t in enumerate(es.get("tasks") or [], 1)}
    arms = es.get("arms") or []
    amap: dict = {}
    n = k = 0
    for a in arms:
        if a.get("is_reference"):
            k += 1
            amap[a["arm_id"]] = "C" if k == 1 else f"C{k}"
        else:
            n += 1
            amap[a["arm_id"]] = f"T{n}"
    for t in es.get("tasks") or []:
        t["task_id"] = tmap.get(t["task_id"], t["task_id"])
    for a in arms:
        a["arm_id"] = amap.get(a["arm_id"], a["arm_id"])
        a["task_ids"] = [tmap.get(x, x) for x in a.get("task_ids") or []]
    for e in es.get("estimands") or []:
        e["task_id"] = tmap.get(e["task_id"], e["task_id"])
        e["arm_id"], e["vs_arm_id"] = amap.get(e["arm_id"], e["arm_id"]), amap.get(e["vs_arm_id"], e["vs_arm_id"])
    for t in de.get("tasks") or []:
        t["task_id"] = tmap.get(t.get("task_id"), t.get("task_id"))
        for a in t.get("arms") or []:
            a["arm_id"] = amap.get(a.get("arm_id"), a.get("arm_id"))
    return es, em, de
