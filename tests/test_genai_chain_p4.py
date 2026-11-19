"""The claims pass, the headline-plan pass, the route compute and the passes check of the
GenAI-at-work chain: replayed on the review project's cached answers when the project is on
this machine (the user texts must hash to the project's cache keys, so the prompts are byte for
byte the project's), the routing against a v20 import file, the check's grammar and findings."""
from __future__ import annotations

import copy
import json
import os
import sys
from collections import Counter

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from paperlens import chains, checks  # noqa: E402
from paperlens import workflow_spec as ws  # noqa: E402
from paperlens.chains.genai_work import claims as CL  # noqa: E402
from paperlens.chains.genai_work import document as D  # noqa: E402
from paperlens.chains.genai_work import hashes  # noqa: E402
from paperlens.chains.genai_work import headlines as H  # noqa: E402
from paperlens.chains.genai_work import passes_check as PC  # noqa: E402
from paperlens.chains.genai_work import plan as PL  # noqa: E402
from paperlens.chains.genai_work import record as R  # noqa: E402
from paperlens.chains.genai_work import route as RT  # noqa: E402
from paperlens.chains.genai_work import text as X  # noqa: E402
from test_genai_chain_record import RID2, _pages, _plan, _split  # noqa: E402
from test_genai_chain_replay import PROJECT, RID, _project_ok  # noqa: E402

PROMPT_NAMES = ("claims_abstract", "claims_map", "plan_task_evidence", "plan_unit_attr", "plan_atomic", "plan_discussion", "plan_chain")


def _pool(rid):
    f = PROJECT / "data" / "abstracts" / "pool.jsonl"
    if not f.exists():
        return {}
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("record_id") == rid:
            return e
    return {}


def _cached(rid):
    """The project's cached answers of the claims and plan calls, by their keys."""
    out = {}
    for d in ("claims_gemini-3.1-pro-preview", "trial_plan_gemini-3.1-pro-preview"):
        f = PROJECT / "labeling" / "llm" / d / f"{rid}.jsonl"
        if f.exists():
            for line in f.read_text(encoding="utf-8").splitlines():
                r = json.loads(line)
                out[r["key"]] = r["answer"]
    return out


class CachedCtx:
    """A pass context whose model is the project's cache: a call is answered when its user text
    hashes to a cached key (the project's own); with ``by_name`` a call whose key is not cached
    takes the last cached answer of the same call name (a cache made under an earlier version
    of the project's code). Every key asked is recorded."""
    def __init__(self, rid, reads, pool=None, by_name=False):
        pool = pool if pool is not None else _pool(rid)
        self.reads, self.pages = reads, _pages(rid).list
        self.paper = {"title": pool.get("title") or "", "abstract": pool.get("abstract") or ""}
        self.params, self.log_lines, self.calls, self.answers, self.by_name = {}, [], [], _cached(rid), by_name
        self.by_prompt = {hashes.prompt(n): n for n in PROMPT_NAMES}

    @property
    def title(self):
        return self.paper["title"]

    def read(self, name):
        return self.reads[name]

    def log(self, line):
        self.log_lines.append(line)

    def call(self, system, user, schema, *, max_tokens=None):
        name = self.by_prompt[system]
        key = CL.cache_key({"claims_abstract": "abstract", "claims_map": "map"}[name], user) if name.startswith("claims") else PL.cache_key(name, user)
        self.calls.append(key)
        if key in self.answers:
            return copy.deepcopy(self.answers[key])
        same = [k for k in self.answers if k.split(":")[0] == key.split(":")[0]]
        if self.by_name and same:
            return copy.deepcopy(self.answers[same[-1]])
        raise AssertionError(f"no cached answer for {key} ({name}); cached: {sorted(self.answers)}")


def _strip(o, keys):
    if isinstance(o, dict):
        return {k: _strip(v, keys) for k, v in o.items() if k not in keys}
    return [_strip(x, keys) for x in o] if isinstance(o, list) else o


@pytest.mark.skipif(not _project_ok(), reason="the review project is not on this machine")
@pytest.mark.parametrize("rid", [RID, RID2])
def test_the_claims_pass_replays_the_projects_cached_calls(rid) -> None:
    ctx = CachedCtx(rid, {"estimands": _split(rid, "estimands"), "estimates": _split(rid, "estimates")})
    out = CL.claims(ctx)
    want = _split(rid, "claims")
    assert [k.split(":")[0] for k in ctx.calls] == ["abstract", "map"]
    for k in ("status", "sentences", "intro_sentences", "estimates_claimed", "main_unclaimed", "counts"):
        assert json.dumps(out[k], sort_keys=True) == json.dumps(want[k], sort_keys=True), k
    assert _strip(out["abstract"], ("lines",)) == _strip(want["abstract"], ("lines",))          # the line numbers depend on the page markers' blank lines
    assert out["intro"]["how"] == want["intro"]["how"] and out["intro"]["pages"] == want["intro"]["pages"]


# the project's cached plan calls were made before its last code changes, so they cannot answer the
# pass's requests by key; the project's OWN current code renders these first requests (computed on
# 20 Nov 2026 by running trial_plan.py's chunking on the two papers): the port's text must hash alike
PLAN_FIRST_KEYS = {RID: "task_chunks:a341cd38", RID2: "task_chunks:ff4b4de8"}


@pytest.mark.skipif(not _project_ok(), reason="the review project is not on this machine")
@pytest.mark.parametrize("rid", [RID, RID2])
def test_the_headline_plan_pass_renders_the_projects_requests_and_applies_its_rule(rid) -> None:
    reads = {n: _split(rid, n) for n in ("exhibits", "estimands", "estimates", "design", "claims")}
    ctx = CachedCtx(rid, reads, by_name=True)
    out = PL.headline_plan(ctx)
    assert ctx.calls[0] == PLAN_FIRST_KEYS[rid], ctx.calls
    assert [k.split(":")[0] for k in ctx.calls[:3]] == ["task_chunks", "unit_attr", "atomic"]
    want = _plan(rid)
    assert all(d["branch"] for d in out["decisions"]) and len(out["units"]) >= 1
    for k in ("appendix", "conclusion_sentences"):                                           # from the text alone
        assert json.dumps(out[k], sort_keys=True) == json.dumps(want[k], sort_keys=True), k
    if [e["estimand_id"] for e in want["estimands"]] != [e["estimand_id"] for e in reads["estimands"]["estimands"]]:
        return                                   # the project's plan file predates a re-extraction of this paper: its units are another record's
    strip_attr = lambda us: [{k: v for k, v in u.items() if k != "attributes"} for u in us]  # noqa: E731
    assert json.dumps(strip_attr(out["units"]), sort_keys=True) == json.dumps(strip_attr(want["units"]), sort_keys=True)
    assert out["uncovered_units"] == want["uncovered_units"] and [d["unit"] for d in out["decisions"]] == [d["unit"] for d in want["decisions"]]
    rec = _split(rid, "record")["record"]
    assert H.headlines(rec, out)["headlines"] == H.headlines(rec, want)["headlines"]


def test_the_criteria_selftest_holds_over_the_port() -> None:
    rec = {"study": {"sample_size": {"value": 137}, "research_design": {"value": "online_rct"}, "identification_strategy": {"value": "random_assignment"}},
           "tasks": [{"arms": [{"arm_id": "C", "is_baseline": True, "control_type": {"value": "other_ai"}, "n_arm": {"value": 60}}, {"arm_id": "T", "is_baseline": False}],
                      "results": [{"outcome_class": {"value": "subjective"}, "n": 70}]}]}
    codes = dict(RT.criteria(rec, {"outcomes": [{"outcome_class": "subjective"}]}))
    assert set(codes) == {"C1_no_noai_reference", "C2_no_performance_outcome", "C5_all_sizes_below_200"}
    rec["tasks"][0]["arms"][0]["control_type"] = {"value": "no_ai_concurrent"}; rec["tasks"][0]["results"][0]["n"] = 240
    rec["tasks"][0]["results"][0]["outcome_class"] = {"value": "performance"}
    assert RT.criteria(rec) == []
    rec["study"]["sample_size"] = {"value": -1}; rec["tasks"][0]["arms"][0]["n_arm"] = None; rec["tasks"][0]["results"][0]["n"] = None
    assert RT.criteria(rec) == [("C5_size_unknown", "no sample size, arm size or row n anywhere in the record")]


@pytest.mark.skipif(not _project_ok() or not (PROJECT / "labeling" / "metalens" / "import_pilot" / f"{RID2}.json").exists(), reason="the review project's import file is not on this machine")
def test_the_routing_reproduces_the_projects_flags_and_decision() -> None:
    want = json.loads((PROJECT / "labeling" / "metalens" / "import_pilot" / f"{RID2}.json").read_text(encoding="utf-8"))["paper_metadata"]
    P = _pages(RID2)
    fin = R.finish(_split(RID2, "exhibits"), _split(RID2, "estimands"), _split(RID2, "estimates"), _split(RID2, "design"), P)
    fin["record"] = _split(RID2, "record")["record"]                                 # the readings' annotations the stored record carries
    fin["issues"] += [i for i in _split(RID2, "record")["issues"] if i["code"] in ("runs_disagree", "not_in_all_runs", "task_count_differs", "runs_settled", "runs_unsettled", "runs_wording")]
    heads = H.headlines(fin["record"], _plan(RID2))
    out = RT.decide(fin, heads, 6, P.pages)
    theirs = [f for f in want["flags"] if f["kind"] != "coder_flag"]
    assert json.dumps(out["flags"], sort_keys=True) == json.dumps(theirs, sort_keys=True)
    assert out["routed"] == want["routed"] and out["route_reasons"] == (want["route_reasons"] or []) and out["triage_score"] == want["triage_score"]


def _synthetic():
    es = {"tasks": [{"task_id": "T1", "name": "Email task", "description": "Improve an email", "sample": "244 students"}],
          "arms": [{"arm_id": "C", "label": "Control", "is_reference": True, "control_type": "no_ai_concurrent", "task_ids": ["T1"], "quote": "the control group wrote the email alone", "page": 1, "check": "page"},
                   {"arm_id": "T1", "label": "AI", "is_reference": False, "treatment": "access to ChatGPT", "task_ids": ["T1"], "quote": "the treatment group had access to ChatGPT", "page": 1, "check": "page"}],
          "outcomes": [{"outcome_id": "O1", "measure": "time to complete", "how_measured": "minutes", "outcome_class": "performance", "measure_kind": "duration", "finding_type": "productivity_time", "quote": "time to complete in minutes", "page": 1, "check": "page"}],
          "estimands": [{"estimand_id": "E1", "task_id": "T1", "arm_id": "T1", "vs_arm_id": "C", "outcome_id": "O1", "subgroup": None, "horizon": None, "contrast_type": "ai_vs_no_ai", "statement": "s"}]}
    em = {"estimates": [{"estimate_id": "x1", "estimand_id": "E1", "role": "main", "source": {"exhibit": "Table 9", "row": "AI", "column": "(1)", "page": 1}, "effect_value": -3.0, "effect_type": "mean_difference", "se": 3.0, "p_reported": "***",
                         "quote": "AI cut the time by 3 minutes (SE 3.0)", "page": 1, "estimand_type": "itt", "_runs": {"found": 1, "of": 2, "alt": {}}}], "unmatched_estimands": [], "orphan_numbers": []}
    de = {"study": {"scale": {"value": "micro", "quote": "a lab study", "page": 1, "proposed": None}, "sample_size": {"value": 40, "quote": "40 students", "page": 1}}, "tasks": [{"task_id": "T1", "arms": []}]}
    ex = {"tables": [], "figures": [], "text_numbers": []}
    page = "A lab study with 40 students. The control group wrote the email alone; the treatment group had access to ChatGPT. We measured time to complete in minutes. AI cut the time by 3 minutes (SE 3.0)."
    return ex, es, em, de, [page]


def test_the_passes_check_turns_the_projects_issues_into_findings_per_paper() -> None:
    ex, es, em, de, pages = _synthetic()
    inputs = [{"name": n, "pass": f"genai_work.{n}", "outputs": {"sha1": o}, "stability": {"sha1": st}, "pages": {"sha1": pages}}
              for n, o, st in (("exhibits", ex, None), ("estimands", es, None), ("estimates", em, {"issues": [{"severity": "warn", "path": "estimates[0]", "code": "not_in_all_runs", "message": "estimate x1 has no counterpart in 1 of 2 runs"}]}), ("design", de, None))]
    found = PC.check_genai_passes(inputs, {})
    codes = Counter(f["code"] for f in found)
    assert all(f["pdf_sha256"] == "sha1" for f in found) and codes["source_exhibit_unknown"] == 1 and codes["stars_se_inconsistent"] == 1 and codes["not_in_all_runs"] == 1
    assert next(f for f in found if f["code"] == "source_exhibit_unknown")["severity"] == "error" and next(f for f in found if f["code"] == "not_in_all_runs")["path"] == "estimates:estimates[0]"
    assert all(f["code"] in PC.CODES for f in found), {f["code"] for f in found} - set(PC.CODES)
    assert PC.check_genai_passes(inputs[:3], {}) == []                                   # the design pass missing: nothing to assemble
    reg = checks.REGISTRY["genai-work-passes"]
    assert reg["inputs"] == 4 and reg["applies_to"]["unit"] == "papers" and reg["codes"] is PC.CODES and reg["fn"](inputs, {}) == found


def test_the_grammar_takes_the_passes_check_over_the_four_pass_steps() -> None:
    chains.load()
    steps = [{"id": "exhibits", "kind": "pass", "pass": chains.pin("genai_work.exhibits")},
             {"id": "estimands", "kind": "pass", "pass": chains.pin("genai_work.estimands"), "reads": ["exhibits"]},
             {"id": "estimates", "kind": "pass", "pass": chains.pin("genai_work.estimates"), "reads": ["exhibits", "estimands"]},
             {"id": "design", "kind": "pass", "pass": chains.pin("genai_work.design"), "reads": ["estimands"]},
             {"id": "passes_check", "kind": "check", "check": "genai-work-passes", "over": ["exhibits", "estimands", "estimates", "design"], "on_fail": "note", "severity": "error"},
             {"id": "release", "kind": "release", "gate": []}]
    spec = ws.normalize({"format": 1, "id": "gw-check", "meta": {"title": "t"}, "steps": steps})
    assert ws.validate(spec)[0] == []
    bad = json.loads(json.dumps(spec)); bad["steps"][4]["over"] = ["estimands", "exhibits", "estimates", "design"]
    assert any("reads genai_work.exhibits there" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][4]["over"] = ["exhibits", "estimands", "estimates"]
    assert any("reads 4 step(s)" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][4]["over"] = ["exhibits", "estimands", "estimates", "release"]
    assert any("is not an earlier pass step" in e for e in ws.validate(bad)[0])
    # the record check stays a check over documents: a pass step is no input for it
    bad = json.loads(json.dumps(spec)); bad["steps"][4] = {"id": "rc", "kind": "check", "check": "genai-work-record", "over": ["design"], "on_fail": "note", "severity": "error"}
    assert any("is not an earlier extract or derive step" in e for e in ws.validate(bad)[0])


def test_the_route_compute_edits_the_paper_fields_from_the_passes() -> None:
    chains.load()
    e = chains.get("genai_work.route")
    assert e["kind"] == "compute" and e["produces"] == "fields" and e["reads"] == ["exhibits", "estimands", "estimates", "design", "headline_plan"] and e["params"]["threshold"]["default"] == 6
    ex, es, em, de, pages = _synthetic()

    class Ctx:
        reads = {"exhibits": ex, "estimands": es, "estimates": em, "design": de}
        paper, params, record, log_lines = {"title": "T"}, {"threshold": 1}, None, []

        def __init__(self):
            self.pages = pages

        def read(self, n):
            return self.reads[n]

        def log(self, line):
            self.log_lines.append(line)
    out = e["fn"](Ctx())
    p = out["paper"]
    assert p["routed"] is True and p["route_reasons"] == ["C5_all_sizes_below_200", "triage_headline"] and p["triage_score"] >= 1
    kinds = [f["kind"] for f in p["flags"]]
    assert kinds[0] == "criterion" and "check_fail" in kinds and all(f["flag_id"] == f"F{i + 1}" for i, f in enumerate(p["flags"]))
    crit = next(f for f in p["flags"] if f["kind"] == "criterion")
    assert crit["code"] == "C5_all_sizes_below_200" and crit["page"] == 1 and crit["source"] == "the record"
    fail = next(f for f in p["flags"] if f["kind"] == "check_fail")
    assert fail["estimate_id"] == "x1" and fail["group"].startswith("T1 · AI vs Control · time to complete") and fail["source"] == "Table 9, col (1), row AI"
    assert [f["code"] for f in out["findings"] if f["code"].startswith("C")] == ["C5_all_sizes_below_200"] and any(f["code"] == "triage_headline" for f in out["findings"])
    assert out["records"] == {} and Ctx.log_lines and "routed" in Ctx.log_lines[0]


def test_the_claims_and_plan_passes_are_registered_with_their_prompts() -> None:
    chains.load()
    c, p = chains.get("genai_work.claims"), chains.get("genai_work.headline_plan")
    assert c["reads"] == ["estimands", "estimates"] and c["calls_per_paper"] == 2 and set(c["prompts"]) == {"abstract", "abstract_schema", "map"}
    assert p["reads"] == ["exhibits", "estimands", "estimates", "design", "claims"] and p["calls_per_paper"] == 4 and len(p["prompts"]) == 8
    assert chains.pin("genai_work.claims").startswith("genai_work.claims@") and chains.prompt_sha("genai_work.headline_plan") != chains.prompt_sha("genai_work.claims")
    assert hashes.claims_hash() == hashes.exported()["claims"]
    # a paper without an abstract: the pass answers without a call
    ex, es, em, de, pages = _synthetic()

    class Ctx:
        reads, paper, params, log_lines, calls, title = {"estimands": es, "estimates": em}, {"title": "T", "abstract": ""}, {}, [], [], "T"

        def __init__(self):
            self.pages = ["No abstract here. Just a body of text with the methods."]

        def read(self, n):
            return self.reads[n]

        def log(self, line):
            self.log_lines.append(line)

        def call(self, *a, **k):
            raise AssertionError("no call expected")
    out = CL.claims(Ctx())
    assert out["status"] == "no_abstract" and out["sentences"] == [] and out["main_unclaimed"] == ["x1"]


def test_a_check_over_pass_steps_runs_when_the_passes_land(tmp_path, monkeypatch) -> None:
    """The engine path: a workflow whose check reads the four pass steps; the outputs supplied from
    outside, the paper taken in, the check's findings stored per paper (pdf sha)."""
    import uuid

    from test_analysis_table import _db_ok
    from test_passes import _dataset
    from test_workflows import _client, _force_local, _pdf, _sha

    from paperlens import records, workflows
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    chains.load()
    wdir = tmp_path / "wf"; wdir.mkdir(exist_ok=True)
    spec = {"format": 1, "id": "gw-passes-check", "meta": {"title": "GenAI passes + check"},
            "steps": [{"id": "exhibits", "kind": "pass", "pass": chains.pin("genai_work.exhibits")},
                      {"id": "estimands", "kind": "pass", "pass": chains.pin("genai_work.estimands"), "reads": ["exhibits"]},
                      {"id": "estimates", "kind": "pass", "pass": chains.pin("genai_work.estimates"), "reads": ["exhibits", "estimands"]},
                      {"id": "design", "kind": "pass", "pass": chains.pin("genai_work.design"), "reads": ["estimands"]},
                      {"id": "passes_check", "kind": "check", "check": "genai-work-passes", "over": ["exhibits", "estimands", "estimates", "design"], "on_fail": "note", "severity": "error"},
                      {"id": "release", "kind": "release", "gate": []}]}
    (wdir / "gw-passes-check.json").write_text(json.dumps(spec))
    monkeypatch.setenv("PAPERLENS_WORKFLOW_DIRS", str(wdir))
    assert "gw-passes-check" in workflows.load_all()
    sess = f"gwc-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess)
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "gw-passes-check"}).status_code == 200
    pdf = _pdf("theta"); sha = _sha(pdf)
    plan = c.post(f"/api/datasets/{ds}/workflow/plan", headers=H, json={"new_pdfs": [{"sha256": sha, "filename": "theta.pdf"}]}).json()
    chk = next(s_ for s_ in plan["steps"] if s_["id"] == "passes_check")
    assert chk["state"] == "blocked" and "has not run" in chk["because"][0]
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": sha, "filename": "theta.pdf"}]}).json(); rid = run["id"]
    ex, es, em, de, _pages = _synthetic()
    for sid, out, code in (("exhibits", ex, "genai_work.exhibits"), ("estimands", es, "genai_work.estimands"), ("estimates", em, "genai_work.estimates"), ("design", de, "genai_work.design")):
        r = c.post(f"/api/workflow-runs/{rid}/passes/{sid}/{sha}", headers=H, json={"output": out, "prompt_sha256": chains.prompt_sha(code)})
        assert r.status_code == 200 and r.json()["supplied"] is True, r.text
    # every pass of the only staged paper supplied: the passes are done, the check runs, the run finishes (no PDF needed for a
    # workflow without a document step; a chain with an assemble step takes the PDF first — the seeder's order)
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "done", (view["status"], view.get("error"))
    st = next(s_ for s_ in view["steps"] if s_["step_id"] == "passes_check")
    assert st["status"] == "done" and st["outputs"]["findings"] >= 2 and st["outputs"]["by_code"].get("source_exhibit_unknown") == 1 and st["outputs"]["n_checked"] == 1
    rows = conn.execute("""SELECT f.pdf_sha256, f.code, f.severity, f.path FROM workflow_finding f WHERE f.run_id = %s::uuid AND f.step_id = 'passes_check' ORDER BY f.code""", (rid,)).fetchall()
    assert rows and all(r_[0] == sha for r_ in rows) and ("source_exhibit_unknown", "error", "estimates[x1]") in {(r_[1], r_[2], r_[3]) for r_ in rows}
