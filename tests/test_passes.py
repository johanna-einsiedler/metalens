"""PASS steps (paperlens/passes.py, paperlens/chains): registered code runs per paper in its own
job, may call the model, reads the page-marked text and earlier passes' outputs, and writes a
JSON output stored per (run, step, paper) with its prompt and inputs shas — reused across runs
without a call, or supplied from outside. The step is the barrier across papers. Model calls
are faked at ``providers.generate_json``. Skips without Postgres."""
from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from test_analysis_table import _db_ok  # noqa: E402
from test_workflows import _client, _force_local, _pdf, _sha  # noqa: E402

from paperlens import chains, passes, presets, providers, records, workflows  # noqa: E402
from paperlens import workflow_spec as ws  # noqa: E402


def _workflow(tmp_path, monkeypatch, *, words_params=None) -> dict:
    wdir = tmp_path / "wf"; wdir.mkdir(exist_ok=True)
    spec = {"format": 1, "id": "demo-pass", "meta": {"title": "Demo passes"},
            "steps": [{"id": "title", "kind": "pass", "pass": chains.pin("_demo.title")},
                      {"id": "words", "kind": "pass", "pass": chains.pin("_demo.words"), "reads": ["title"], **({"params": words_params} if words_params else {})},
                      {"id": "release", "kind": "release", "gate": []}]}
    (wdir / "demo-pass.json").write_text(json.dumps(spec))
    monkeypatch.setenv("PAPERLENS_WORKFLOW_DIRS", str(wdir))
    return workflows.load_all()["demo-pass"]


class _Fake:
    """A model that titles a paper by its first page and names one word per page; it can fail
    for one paper (by a marker in the user text) and counts its calls."""
    def __init__(self):
        self.calls = []
        self.fail_on: str | None = None

    def __call__(self, model, api_key, *, system, user, schema, max_tokens, base_url=None, **kw):
        self.calls.append({"model": model, "api_key": api_key, "system": system, "user": user[:80], "max_tokens": max_tokens})
        if self.fail_on and self.fail_on in user:
            raise ValueError("the model refused this paper")
        if system.startswith("Read the paper"):
            first = user.split("=== PAGE 1 ===", 1)[-1].strip().split("\n", 1)[0]
            return json.dumps({"title": first.strip() or "untitled", "words": [{"id": "w1", "word": "one"}]}), "stop", {"prompt": 10, "completion": 2, "total": 12}, "fake-1"
        n = int(user.split("Pages: ", 1)[1].strip() or 1)
        return json.dumps({"words": [{"title_id": "w1", "word": f"p{i + 1}"} for i in range(n)]}), "stop", {"prompt": 5, "completion": 1, "total": 6}, "fake-1"


def _dataset(conn, sess: str) -> str:
    pin = presets.emit_schema_row("econ-regression-tables")["schema_id"]
    run = presets.resolve_run(conn, schema_id=pin, params={})
    with conn.transaction():
        records.upsert_schema(conn, run.schema_id, run.field_defs)
    ds = records.create_dataset(conn, title="Demo passes", schema_id=pin, session_id=sess)
    conn.commit()
    return ds["id"]


def test_the_grammar_pins_a_pass_by_its_prompts_and_checks_its_reads(tmp_path, monkeypatch) -> None:
    spec = ws.normalize(json.loads(json.dumps(_workflow(tmp_path, monkeypatch))))
    assert ws.validate(spec)[0] == []
    by = ws.steps_by_id(spec)
    assert by["title"]["k"] == 1 and by["words"]["reads"] == ["title"] and by["words"]["params"] == {}
    bad = json.loads(json.dumps(spec)); bad["steps"][0]["pass"] = "_demo.title@00000000"
    assert any("not the pass's current pin" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][1]["reads"] = []
    assert any("reads 'title'" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][0]["reads"] = ["words"]
    assert any("not an earlier pass step" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][0]["k"] = 2
    assert any("match must name a matcher" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][1]["params"] = {"style": 3}
    assert any("params.style must be a string" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][1]["params"] = {"nope": "x"}
    assert any("takes no 'nope'" in e for e in ws.validate(bad)[0])
    # the model is the run's choice, not the workflow's promise; a prompt edit moves the id
    with_model = json.loads(json.dumps(spec)); with_model["steps"][0]["model"] = "gemini-3.1-pro-preview"
    assert ws.workflow_id(with_model) == ws.workflow_id(spec)
    repinned = json.loads(json.dumps(spec)); repinned["steps"][0]["pass"] = "_demo.title@deadbeef"
    assert ws.workflow_id(repinned) != ws.workflow_id(spec)
    assert chains.pin_ok(chains.pin("_demo.title")) and not chains.pin_ok("_demo.title@deadbeef") and not chains.pin_ok("nope.x@00000000")


def test_a_papers_passes_run_in_order_in_one_job_and_the_step_is_the_barrier(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    conn = records.connect(); records.init_db(conn)
    _workflow(tmp_path, monkeypatch, words_params={"style": "terse"})
    sess = f"pass-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess)
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-pass"}).status_code == 200
    pdfs = [_pdf("alpha"), _pdf("beta")]
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in zip(("alpha", "beta"), pdfs)]
    plan = c.post(f"/api/datasets/{ds}/workflow/plan", headers=H, json={"new_pdfs": staged}).json()
    assert [s["kind"] for s in plan["steps"]] == ["pass", "pass", "release"]
    assert plan["steps"][0]["work"]["extract"] == 2 and plan["steps"][0]["state"] == "missing" and plan["n_calls"] == 4
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid = run["id"]
    assert run["status"] == "running" and run["jobs"]["total"] == 4                        # one job per (paper, pass step)
    # a paper outside the plan is refused; a planned one runs both passes in one go (inline here)
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("zz.pdf", _pdf("zz"), "application/pdf")}, data={"api_key": "k"})
    assert r.status_code == 409
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("alpha.pdf", pdfs[0], "application/pdf")})
    assert r.status_code == 422                                                              # an own-key run needs the key with the paper
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("alpha.pdf", pdfs[0], "application/pdf")}, data={"api_key": "k", "model": "fake-model"})
    assert r.status_code == 200, r.text
    assert r.json()["done"] == ["title", "words"] and r.json()["n_pages"] >= 1 and len(fake.calls) == 2
    assert fake.calls[0]["api_key"] == "k" and fake.calls[0]["model"] == "fake-model" and fake.calls[0]["max_tokens"] == 60_000
    assert "=== PAGE 1 ===" in fake.calls[0]["user"] or fake.calls[0]["user"].startswith("Pages:")
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "running" and view["steps"][0]["status"] == "running"             # the barrier: beta has not arrived
    assert view["jobs"]["done"] == 2 and view["jobs"]["pending"] == 2
    now = view["now"]
    assert now["phase"] == "waiting for uploads" and now["waiting_for_you"] is True and now["uploads"] == [{"step": "title", "label": "Title", "kind": "pass", "done": 1, "total": 2, "pending": 1}]
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("beta.pdf", pdfs[1], "application/pdf")}, data={"api_key": "k", "model": "fake-model"})
    assert r.status_code == 200, r.text
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "done", view
    title_sr, words_sr = view["steps"][0], view["steps"][1]
    assert title_sr["status"] == "done" and title_sr["outputs"]["n_papers"] == 2 and title_sr["outputs"]["n_calls"] == 2 and title_sr["n_calls"] == 2
    assert title_sr["prompt_sha256"] == chains.prompt_sha("_demo.title") and title_sr["outputs"]["reused"] == 0 and title_sr["outputs"]["usage"]["total"] == 24
    assert words_sr["outputs"]["n_papers"] == 2 and words_sr["outputs"]["code"] == "_demo.words"
    # the outputs: the JSON each pass wrote, with its provenance
    lst = c.get(f"/api/workflow-runs/{rid}/passes/title", headers=H).json()
    assert len(lst["outputs"]) == 2 and all(o["status"] == "done" and o["role"] == "primary" and "output" not in o for o in lst["outputs"])
    one = c.get(f"/api/workflow-runs/{rid}/passes/words/{staged[0]['sha256']}", headers=H).json()
    assert one["output"]["title_len"] > 0 and len(one["output"]["words"]) == one["output"]["title_len"] * 0 + len(one["output"]["words"])
    assert one["prompt_sha256"] == chains.prompt_sha("_demo.words") and one["inputs_sha256"] and one["n_calls"] == 1 and one["model"] == "fake-model"
    assert c.get(f"/api/workflow-runs/{rid}/passes/words/{staged[0]['sha256']}", headers=H, params={"pass_index": 3}).status_code == 404
    papers = c.get(f"/api/workflow-runs/{rid}/papers", headers=H).json()["papers"]
    assert {p["pdf_sha256"] for p in papers} == {s["sha256"] for s in staged} and all(p["n_pages"] >= 1 and p["status"] == "ready" for p in papers)
    # a second run over the same papers reuses every output: no call, the provenance names the source
    n_before = len(fake.calls)
    run2 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid2 = run2["id"]
    for name, b in zip(("alpha", "beta"), pdfs):
        assert c.post(f"/api/workflow-runs/{rid2}/papers", headers=H, files={"pdf": (f"{name}.pdf", b, "application/pdf")}, data={"api_key": "k", "model": "fake-model"}).status_code == 200
    view2 = c.get(f"/api/workflow-runs/{rid2}", headers=H).json()
    assert view2["status"] == "done" and len(fake.calls) == n_before
    assert view2["steps"][0]["outputs"]["reused"] == 2 and view2["steps"][0]["outputs"]["n_calls"] == 0
    again = c.get(f"/api/workflow-runs/{rid2}/passes/words/{staged[1]['sha256']}", headers=H).json()
    assert again["reused_from"] == c.get(f"/api/workflow-runs/{rid}/passes/words/{staged[1]['sha256']}", headers=H).json()["id"] and again["n_calls"] == 0
    # a changed parameter changes the inputs sha: the words pass runs again, the title pass is still reused
    _workflow(tmp_path, monkeypatch, words_params={"style": "florid"})
    run3 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged[:1]}).json(); rid3 = run3["id"]
    assert c.post(f"/api/workflow-runs/{rid3}/papers", headers=H, files={"pdf": ("alpha.pdf", pdfs[0], "application/pdf")}, data={"api_key": "k", "model": "fake-model"}).status_code == 200
    view3 = c.get(f"/api/workflow-runs/{rid3}", headers=H).json()
    assert view3["status"] == "done" and view3["steps"][0]["outputs"]["reused"] == 1 and view3["steps"][1]["outputs"]["reused"] == 0 and len(fake.calls) == n_before + 1
    conn.close()


def test_a_supplied_output_stands_in_for_the_call_and_a_failed_paper_stops_its_chain(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    conn = records.connect(); records.init_db(conn)
    _workflow(tmp_path, monkeypatch)
    sess = f"pass-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess)
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-pass"}).status_code == 200
    pdfs = {"gamma": _pdf("gamma"), "delta": _pdf("delta"), "omega": _pdf("omega")}
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in pdfs.items()]
    sha = {n: _sha(b) for n, b in pdfs.items()}
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid = run["id"]
    # gamma's title comes from outside (the pipeline's own file): the words pass reads it, no title call is made
    r = c.post(f"/api/workflow-runs/{rid}/passes/title/{sha['gamma']}", headers=H, json={"output": {"title": "Supplied title", "n_pages": 1, "words": [{"id": "w1", "word": "s"}]}, "prompt_sha256": "810861b1ac"})
    assert r.status_code == 200 and r.json()["supplied"] is True and r.json()["prompt_sha256"] == "810861b1ac"
    # a NUL in a supplied output (PDF text debris) is dropped, not a 500: jsonb cannot hold \u0000
    r = c.post(f"/api/workflow-runs/{rid}/passes/title/{sha['omega']}", headers=H,
               json={"output": {"title": "A\u0000B", "n_pages": 1, "words": [{"id": "w1", "word": "x\u0000"}], "k\u0000ey": ["\u0000"]}, "role": "run", "pass_index": 1})
    assert r.status_code == 200, r.text
    got = c.get(f"/api/workflow-runs/{rid}/passes/title/{sha['omega']}", headers=H, params={"role": "run", "pass_index": 1}).json()["output"]
    assert got["title"] == "AB" and got["words"][0]["word"] == "x" and got["key"] == [""]
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("gamma.pdf", pdfs["gamma"], "application/pdf")}, data={"api_key": "k", "model": "m"})
    assert r.status_code == 200 and r.json()["skipped"] == ["title"] and r.json()["done"] == ["words"] and len(fake.calls) == 1
    words = c.get(f"/api/workflow-runs/{rid}/passes/words/{sha['gamma']}", headers=H).json()
    assert words["output"]["title_len"] == len("Supplied title")
    # delta's title call fails: its words job fails with the reason, the other papers go on
    fake.fail_on = "delta"
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("delta.pdf", pdfs["delta"], "application/pdf")}, data={"api_key": "k", "model": "m"})
    assert r.status_code == 200 and r.json()["failed"] == {"title": "the model refused this paper"} and r.json()["done"] == []
    js = c.get(f"/api/workflow-runs/{rid}/passes/words", headers=H).json()["jobs"]
    assert next(j for j in js if j["pdf_sha256"] == sha["delta"])["status"] == "failed" and "title failed" in next(j for j in js if j["pdf_sha256"] == sha["delta"])["error"]
    # omega never arrives: the owner closes the uploads and the run finishes on what landed
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "running"
    view = c.post(f"/api/workflow-runs/{rid}/complete", headers=H, json={"step_id": "title"}).json()
    assert view["status"] == "done", view
    t, w = view["steps"][0], view["steps"][1]
    assert t["outputs"]["n_papers"] == 1 and t["outputs"]["supplied"] == 1 and [f["pdf_sha256"] for f in t["outputs"]["failed"]] == [sha["delta"]]
    assert w["outputs"]["n_papers"] == 1 and view["jobs"]["cancelled"] == 2 and view["jobs"]["failed"] == 2
    # a run with nothing landed fails its first pass step
    fake.fail_on = "epsilon"
    eps = _pdf("epsilon")
    staged2 = [{"sha256": _sha(eps), "filename": "epsilon.pdf"}]
    run2 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged2}).json(); rid2 = run2["id"]
    assert c.post(f"/api/workflow-runs/{rid2}/papers", headers=H, files={"pdf": ("epsilon.pdf", eps, "application/pdf")}, data={"api_key": "k", "model": "m"}).status_code == 200
    view2 = c.get(f"/api/workflow-runs/{rid2}", headers=H).json()
    assert view2["status"] == "failed" and view2["steps"][0]["status"] == "failed" and "no paper landed" in (view2["error"] or "")
    conn.close()


def test_the_context_text_marks_pages_and_a_cut_answer_raises(monkeypatch) -> None:
    entry = chains.get("_demo.title")
    gen = lambda system, user, schema, cap: ('{"title": "T"', "length", {}, "fake")   # noqa: E731
    ctx = passes.PassContext(entry=entry, run_id="r", step_id="s", pdf_sha256="x", pages=["first page", "second"], paper={"title": None},
                             params={}, reads={}, model="m", generate=gen)
    assert ctx.text() == "=== PAGE 1 ===\nfirst page\n=== PAGE 2 ===\nsecond" and ctx.text(marker="--- {i} ---").startswith("--- 1 ---")
    with pytest.raises(passes.Truncated) as exc:
        ctx.call("S", "U", None)
    assert exc.value.text.startswith('{"title"') and ctx.calls[0]["finish_reason"] == "length"
    ctx.generate = lambda system, user, schema, cap: {"title": "T"}
    assert ctx.call("S", "U", None) == {"title": "T"} and len(ctx.calls) == 2
    with pytest.raises(KeyError):
        ctx.read("nothing")
    assert passes.parse_json('```json\n{"a": 1}\n```') == {"a": 1} and passes.parse_json('text {"a": [1]} more') == {"a": [1]}


def test_references_across_passes_are_checked_from_the_registration() -> None:
    title_e, words_e = chains.get("_demo.title"), chains.get("_demo.words")
    title = {"title": "T", "n_pages": 2, "words": [{"id": "w1", "word": "a"}, {"id": "w2", "word": "b"}]}
    assert passes.check_refs(title_e, title, {}) == []
    dup = {"title": "T", "words": [{"id": "w1"}, {"id": "w1"}, {"id": None}, {"id": {"x": 1}}]}
    codes = [(f["code"], f["path"]) for f in passes.check_refs(title_e, dup, {})]
    assert codes == [("duplicate_id", "words[1].id"), ("missing_id", "words[2].id"), ("bad_ref", "words[3].id")]
    assert [f["code"] for f in passes.check_refs(title_e, {"title": "T"}, {})] == ["dangling_path"]
    good = {"words": [{"title_id": "w1", "word": "x"}, {"title_id": "w2", "word": "y"}, {"title_id": None}]}
    assert passes.check_refs(words_e, good, {"title": title}, pdf_sha256="abc") == []
    bad = {"words": [{"title_id": "w9", "word": "x"}, {"title_id": 3}]}
    found = passes.check_refs(words_e, bad, {"title": title}, pdf_sha256="abc")
    assert [(f["code"], f["path"], f["severity"], f["pdf_sha256"]) for f in found] == [("dangling_ref", "words[0].title_id", "error", "abc"), ("dangling_ref", "words[1].title_id", "error", "abc")]
    assert found[0]["detail"] == {"value": "w9", "to": "title:words[].id"} and found[0]["pass"] == "_demo.words"
    assert passes.check_refs(words_e, bad, {}) == []                                      # no upstream given: nothing to check against
    assert passes.walk({"a": [{"b": [1, 2]}, {"b": []}]}, "a[].b[]") == [("a[0].b[0]", 1), ("a[0].b[1]", 2)]
    assert passes.walk({"a": {"b": "x"}}, "a.b") == [("a.b", "x")] and passes.walk({"a": "no"}, "a[].b") == []
    with pytest.raises(ValueError):
        passes.walk({}, "a[0].b")


def test_a_dangling_reference_in_a_pass_output_is_a_finding_of_the_step(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    conn = records.connect(); records.init_db(conn)
    _workflow(tmp_path, monkeypatch)
    sess = f"pass-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess)
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-pass"}).status_code == 200
    pdfs = {"rho": _pdf("rho"), "sigma": _pdf("sigma")}
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in pdfs.items()]
    sha = {n: _sha(b) for n, b in pdfs.items()}
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid = run["id"]
    # rho's words point at an id its title never minted; sigma's are fine
    real = fake.__call__
    def twisted(model, api_key, *, system, user, schema, max_tokens, base_url=None, **kw):
        text, finish, usage, served = real(model, api_key, system=system, user=user, schema=schema, max_tokens=max_tokens, base_url=base_url)
        if system.startswith("Return one word") and "rho" in user:
            text = json.dumps({"words": [{"title_id": "w9", "word": "p1"}]})
        return text, finish, usage, served
    monkeypatch.setattr(providers, "generate_json", twisted)
    for n, b in pdfs.items():
        assert c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": (f"{n}.pdf", b, "application/pdf")}, data={"api_key": "k", "model": "m"}).status_code == 200
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "done"
    words = view["steps"][1]
    assert words["outputs"]["findings"] == 1 and words["outputs"]["findings_by_code"] == {"dangling_ref": 1}
    rows = c.get(f"/api/workflow-runs/{rid}/passes/words", headers=H).json()["outputs"]
    assert {r["pdf_sha256"]: r["findings"] for r in rows} == {sha["rho"]: 1, sha["sigma"]: 0}
    f = conn.execute('SELECT pdf_sha256, "check", code, severity, path, detail FROM workflow_finding WHERE run_id = %s::uuid', (rid,)).fetchall()
    assert f == [(sha["rho"], "refs", "dangling_ref", "error", "words[0].title_id", {"value": "w9", "to": "title:words[].id"})]
    # a supplied output is checked the same way
    run2 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged[:1]}).json(); rid2 = run2["id"]
    assert c.post(f"/api/workflow-runs/{rid2}/passes/title/{sha['rho']}", headers=H, json={"output": {"title": "S", "words": [{"id": "a"}, {"id": "a"}]}}).json()["findings"] == 1
    conn.rollback()
    assert conn.execute("SELECT code FROM workflow_finding WHERE run_id = %s::uuid", (rid2,)).fetchall() == [("duplicate_id",)]
    conn.close()


def _chain_workflow(tmp_path, monkeypatch, pin: str) -> dict:
    wdir = tmp_path / "wf"; wdir.mkdir(exist_ok=True)
    spec = {"format": 1, "id": "demo-chain", "meta": {"title": "Demo chain"},
            "steps": [{"id": "title", "kind": "pass", "pass": chains.pin("_demo.title")},
                      {"id": "words", "kind": "pass", "pass": chains.pin("_demo.words"), "reads": ["title"]},
                      {"id": "assemble", "kind": "compute", "fn": "_demo.assemble", "reads": ["title", "words"], "preset": pin, "params": {"field": "statement"}},
                      {"id": "mark", "kind": "compute", "fn": "_demo.mark", "edits": "assemble", "writes": ["sign"], "params": {"value": "+"}},
                      {"id": "review", "kind": "review", "over": ["assemble"], "mode": "all", "unit": "paper"},
                      {"id": "release", "kind": "release", "gate": ["assemble", "review"]}]}
    (wdir / "demo-chain.json").write_text(json.dumps(spec))
    monkeypatch.setenv("PAPERLENS_WORKFLOW_DIRS", str(wdir))
    return workflows.load_all()["demo-chain"]


def test_compute_steps_make_documents_from_the_passes_and_edit_them_through_the_audited_path(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    from test_pipeline import _presets
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    conn = records.connect(); records.init_db(conn)
    pin, _graph = _presets(tmp_path, monkeypatch)
    spec = ws.normalize(json.loads(json.dumps(_chain_workflow(tmp_path, monkeypatch, pin))))
    assert ws.validate(spec)[0] == [], ws.validate(spec)[0]
    assert ws.pins(spec) == [("assemble", pin)] and ws.gate_steps(spec) == ["assemble", "review"]
    bad = json.loads(json.dumps(spec)); bad["steps"][3]["writes"] = ["nope"]
    assert any("not a field of" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][3]["edits"] = "words"
    assert any("edits must name an earlier step whose documents" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][2]["fn"] = "_demo.nothing"
    assert any("fn must name a registered compute" in e for e in ws.validate(bad)[0])
    bad = json.loads(json.dumps(spec)); bad["steps"][2]["edits"] = "words"
    assert any("makes documents, it edits none" in e for e in ws.validate(bad)[0])
    # the run: passes, then the document per paper, then the engine's edits, then the review
    sess = f"chain-{uuid.uuid4().hex[:6]}"
    run_spec = presets.resolve_run(conn, schema_id=pin, params={})
    with conn.transaction():
        records.upsert_schema(conn, run_spec.schema_id, run_spec.field_defs)
    ds = records.create_dataset(conn, title="Demo chain", schema_id=pin, session_id=sess)["id"]; conn.commit()
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-chain"}).status_code == 200
    pdfs = {"kappa": _pdf("kappa"), "lambda": _pdf("lambda")}
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in pdfs.items()]
    plan = c.post(f"/api/datasets/{ds}/workflow/plan", headers=H, json={"new_pdfs": staged}).json()
    by = {s["id"]: s for s in plan["steps"]}
    assert by["assemble"]["kind"] == "compute" and by["assemble"]["work"]["extract"] == 2 and by["assemble"]["state"] == "stale" and by["assemble"]["preset"] == pin
    assert by["mark"]["work"]["extract"] == 2 and by["mark"]["writes"] == ["sign"] and plan["n_calls"] == 4
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid = run["id"]
    assert run["jobs"]["total"] == 8                                                       # 2 papers × (2 passes + 2 computes)
    for n, b in pdfs.items():
        r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": (f"{n}.pdf", b, "application/pdf")}, data={"api_key": "k", "model": "m"})
        assert r.status_code == 200, r.text
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "done", (view["status"], view.get("error"), [(s["step_id"], s["status"], s.get("error")) for s in view["steps"]], [(p["step_id"], p["status"], p["error"]) for p in view["papers"] if p["error"]])
    s_by = {s["step_id"]: s for s in view["steps"]}
    assert s_by["assemble"]["status"] == "done" and s_by["assemble"]["outputs"]["n_papers"] == 2 and len(s_by["assemble"]["outputs"]["document_ids"]) == 2
    assert s_by["mark"]["status"] == "done" and s_by["mark"]["outputs"]["edited_records"] >= 2 and s_by["mark"]["outputs"]["findings"] == 0
    assert s_by["review"]["review"]["total"] == 2 and s_by["review"]["review"]["satisfied"] is False      # the gate holds the dataset's release
    # the documents sit in the dataset under the pin, with the passes as provenance and the edits audited
    docs = c.get("/api/documents", headers=H, params={"dataset": ds}).json()["documents"]
    assert len(docs) == 2
    v = c.get(f"/api/documents/{docs[0]['document_id']}/view", headers=H).json()
    assert v["schema_id"] == pin and [r["field_values"]["sign"] for r in v["records"]] == ["+"] * len(v["records"])
    assert all(r["field_values"]["statement"].startswith("p") for r in v["records"]) and v["chain"]["kind"] == "compute"
    assert [st_["step"] for st_ in v["chain"]["stages"]] == ["title", "words"] and all(st_["prompt_sha256"] for st_ in v["chain"]["stages"])
    assert v["records"][0]["chain"]["kind"] == "compute" and v["records"][0]["chain"]["support"] == {"n": 1, "k": 1}
    ev = conn.execute("""SELECT ve.verifier_kind, ve.status, ve.notes FROM verification_event ve JOIN record r ON r.id = ve.record_id
                         WHERE r.document_id = %s::uuid ORDER BY ve.created_at""", (docs[0]["document_id"],)).fetchall()
    assert ev and all(e == ("engine", "corrected", "compute:_demo.mark") for e in ev)
    assert workflows.basis(conn, ds)["events_max"]                                        # the edits count as corrections: later steps are staled by them
    # the owner's verdicts satisfy the gate
    for d in docs:
        for r_ in c.get(f"/api/documents/{d['document_id']}/view", headers=H).json()["records"]:
            assert c.post(f"/api/records/{r_['id']}/verify", headers=H, json={"status": "verified"}).status_code == 200
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert next(s_ for s_ in view["steps"] if s_["step_id"] == "review")["review"]["satisfied"] is True
    # a second run over the same papers re-makes nothing: the dataset already holds them; with reextract it replaces them
    plan2 = c.post(f"/api/datasets/{ds}/workflow/plan", headers=H, json={"new_pdfs": staged, "reextract": True}).json()
    assert {s["id"]: s["work"]["extract"] for s in plan2["steps"] if s["id"] in ("assemble",)} == {"assemble": 2}
    run2 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged, "reextract": True}).json(); rid2 = run2["id"]
    for n, b in pdfs.items():
        assert c.post(f"/api/workflow-runs/{rid2}/papers", headers=H, files={"pdf": (f"{n}.pdf", b, "application/pdf")}, data={"api_key": "k", "model": "m"}).status_code == 200
    view2 = c.get(f"/api/workflow-runs/{rid2}", headers=H).json()
    s2 = {s["step_id"]: s for s in view2["steps"]}
    assert s2["assemble"]["outputs"]["replaced"] == 2 and s2["title"]["outputs"]["reused"] == 2
    docs2 = c.get("/api/documents", headers=H, params={"dataset": ds}).json()["documents"]
    assert len(docs2) == 2 and {d["document_id"] for d in docs2}.isdisjoint({d["document_id"] for d in docs})
    conn.close()


def test_k_runs_of_a_pass_are_folded_by_the_chains_matcher(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    _force_local(monkeypatch)
    conn = records.connect(); records.init_db(conn)
    wdir = tmp_path / "wf"; wdir.mkdir(exist_ok=True)
    spec = {"format": 1, "id": "demo-k", "meta": {"title": "Demo k"},
            "steps": [{"id": "title", "kind": "pass", "pass": chains.pin("_demo.title"), "k": 3, "match": "_demo.majority"},
                      {"id": "words", "kind": "pass", "pass": chains.pin("_demo.words"), "reads": ["title"]},
                      {"id": "release", "kind": "release", "gate": []}]}
    (wdir / "demo-k.json").write_text(json.dumps(spec))
    monkeypatch.setenv("PAPERLENS_WORKFLOW_DIRS", str(wdir))
    assert ws.validate(ws.normalize(json.loads(json.dumps(spec))))[0] == []
    bad = json.loads(json.dumps(spec)); bad["steps"][0]["match"] = "_demo.title"
    assert any("match must name a matcher" in e for e in ws.validate(ws.normalize(bad))[0])
    # the model answers a different title on its second call for the first paper, fails once for the second
    calls = {"n": 0}
    def fake(model, api_key, *, system, user, schema, max_tokens, base_url=None, **kw):
        calls["n"] += 1
        if system.startswith("Read the paper"):
            if "omicron" in user:
                if calls["n"] % 3 == 2:
                    raise ValueError("refused once")
                return json.dumps({"title": "Omicron", "words": [{"id": "w1", "word": "o"}]}), "stop", {"prompt": 1, "completion": 1, "total": 2}, "fake"
            t = "Theta" if calls["n"] != 2 else "Theta (variant)"
            return json.dumps({"title": t, "words": [{"id": "w1", "word": "t"}]}), "stop", {"prompt": 1, "completion": 1, "total": 2}, "fake"
        return json.dumps({"words": [{"title_id": "w1", "word": "p1"}]}), "stop", {"prompt": 1, "completion": 1, "total": 2}, "fake"
    monkeypatch.setattr(providers, "generate_json", fake)
    sess = f"k-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess)
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-k"}).status_code == 200
    pdfs = {"theta": _pdf("theta"), "omicron": _pdf("omicron")}
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in pdfs.items()]
    sha = {n: _sha(b) for n, b in pdfs.items()}
    plan = c.post(f"/api/datasets/{ds}/workflow/plan", headers=H, json={"new_pdfs": staged}).json()
    assert plan["steps"][0]["calls_per_paper"] == 3 and plan["n_calls"] == 8                   # 3 runs × 2 papers + the words pass
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid = run["id"]
    for n, b in pdfs.items():
        assert c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": (f"{n}.pdf", b, "application/pdf")}, data={"api_key": "k", "model": "m"}).status_code == 200
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "done", (view["status"], view.get("error"))
    t = view["steps"][0]
    assert t["outputs"]["n_papers"] == 2 and t["outputs"]["by_runs"] == {"3": 1, "2": 1} and t["outputs"]["n_calls"] == 5 and t["n_calls"] == 5   # the refused call made no answer
    theta = c.get(f"/api/workflow-runs/{rid}/passes/title/{sha['theta']}", headers=H).json()
    assert theta["output"]["title"] == "Theta" and theta["stability"]["agree"] == 2 and theta["stability"]["distinct"] == 2 and theta["stability"]["n_runs"] == 3
    assert theta["stability"]["matcher"] == "_demo.majority" and theta["n_calls"] == 3 and theta["usage"]["total"] == 6
    r1 = c.get(f"/api/workflow-runs/{rid}/passes/title/{sha['theta']}", headers=H, params={"role": "run", "pass_index": 1}).json()
    assert r1["output"]["title"] == "Theta (variant)" and r1["role"] == "run" and r1["n_calls"] == 1
    omi = c.get(f"/api/workflow-runs/{rid}/passes/title/{sha['omicron']}", headers=H).json()
    assert omi["stability"]["n_runs"] == 2 and omi["stability"]["failed"][0]["error"] == "refused once" and omi["output"]["title"] == "Omicron"
    rows = c.get(f"/api/workflow-runs/{rid}/passes/title", headers=H).json()["outputs"]
    assert sorted((r["pdf_sha256"] == sha["theta"], r["role"], r["pass_index"]) for r in rows) == sorted(
        [(True, "run", 0), (True, "run", 1), (True, "run", 2), (True, "primary", 0), (False, "run", 1), (False, "run", 2), (False, "primary", 0)])   # omicron: its first run was refused
    # the words pass read the primary
    w = c.get(f"/api/workflow-runs/{rid}/passes/words/{sha['theta']}", headers=H).json()
    assert w["output"]["title_len"] == len("Theta")
    # with k = 2 a single failure sinks the paper (both runs are needed)
    spec["steps"][0]["k"] = 2; (wdir / "demo-k.json").write_text(json.dumps(spec))
    calls["n"] = 0
    eps = _pdf("omicron-two")
    run2 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": _sha(eps), "filename": "omicron-two.pdf"}]}).json(); rid2 = run2["id"]
    r = c.post(f"/api/workflow-runs/{rid2}/papers", headers=H, files={"pdf": ("omicron.pdf", eps, "application/pdf")}, data={"api_key": "k", "model": "m"})
    assert r.status_code == 200 and "1 of 2 runs failed" in r.json()["failed"]["title"]
    conn.close()


def test_a_failed_paper_of_a_chain_is_retried_from_the_kept_pdf_and_swept_texts_go(tmp_path, monkeypatch) -> None:
    if not _db_ok():
        pytest.skip("no Postgres")
    from paperlens import retention, storage
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    conn = records.connect(); records.init_db(conn)
    _workflow(tmp_path, monkeypatch)
    sess = f"retry-{uuid.uuid4().hex[:6]}"
    ds = _dataset(conn, sess)
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-pass"}).status_code == 200
    pdf = _pdf("psi"); sha = _sha(pdf)
    run = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": [{"sha256": sha, "filename": "psi.pdf"}]}).json(); rid = run["id"]
    fake.fail_on = "psi"
    r = c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": ("psi.pdf", pdf, "application/pdf")}, data={"api_key": "k", "model": "m"})
    assert r.status_code == 200 and r.json()["failed"] == {"title": "the model refused this paper"}
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["status"] == "failed" and view["jobs"]["failed"] == 2                       # title refused; words failed upstream
    # the retry needs the key on an own-key run; then the paper runs again from the kept PDF and text — but the run is terminal
    assert c.post(f"/api/workflow-runs/{rid}/retry", headers=H, json={"api_key": "k"}).status_code == 409
    # a run that is still live: the second paper fails, is retried, the run finishes
    fake.fail_on = None
    pdfs = {"phi": _pdf("phi"), "chi": _pdf("chi")}
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in pdfs.items()]
    run2 = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json(); rid2 = run2["id"]
    fake.fail_on = "chi"
    for n, b in pdfs.items():
        assert c.post(f"/api/workflow-runs/{rid2}/papers", headers=H, files={"pdf": (f"{n}.pdf", b, "application/pdf")}, data={"api_key": "k", "model": "m"}).status_code == 200
    view = c.get(f"/api/workflow-runs/{rid2}", headers=H).json()
    assert view["status"] == "done" and view["jobs"]["failed"] == 2 and view["steps"][0]["outputs"]["n_papers"] == 1
    fake.fail_on = None
    conn.rollback()
    with conn.transaction():
        conn.execute("UPDATE workflow_run SET status = 'running' WHERE id = %s::uuid", (rid2,))   # a retry while the run is live
    conn.commit()
    r = c.post(f"/api/workflow-runs/{rid2}/retry", headers=H, json={})
    assert r.status_code == 422 and "API key" in r.text                                       # an own-key run: the key comes with the retry
    r = c.post(f"/api/workflow-runs/{rid2}/retry", headers=H, json={"api_key": "k", "model": "m"})
    assert r.status_code == 200 and r.json()["retried"] == [_sha(pdfs["chi"])] and r.json()["skipped"] == []
    view = c.get(f"/api/workflow-runs/{rid2}", headers=H).json()
    assert view["status"] == "done" and view["jobs"]["failed"] == 0 and view["steps"][0]["outputs"]["n_papers"] == 2
    assert c.get(f"/api/workflow-runs/{rid2}/passes/words/{_sha(pdfs['chi'])}", headers=H).json()["output"]["title_len"] > 0
    # the page text of a PDF no live run and no document holds goes with the sweep; the run keeps its paper list
    store = storage.get_store()
    assert store.exists(storage.paper_text_key(sha))
    out = retention.remove_orphans(conn, store)
    assert out["page_texts"] >= 1 and not store.exists(storage.paper_text_key(sha))
    conn.rollback()
    assert conn.execute("SELECT status FROM workflow_paper WHERE run_id = %s::uuid AND pdf_sha256 = %s", (rid, sha)).fetchone() == ("swept",)
    conn.close()


def test_a_compute_steps_papers_run_as_worker_jobs_and_the_step_waits_for_them(tmp_path, monkeypatch) -> None:
    """With a queue, a compute step's paper goes to the worker (``compute_job``) and the request
    returns: the step shows its jobs queued and the run "computing · 0 of 2", until the worker's
    ``passes.run_compute`` lands each paper and advances the run. A retry re-pends a compute job
    without queueing it under the pass job, so advance dispatches it again."""
    if not _db_ok():
        pytest.skip("no Postgres")
    from test_pipeline import _presets
    from paperlens import worker
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    queued: list[tuple] = []
    monkeypatch.setattr(worker, "enqueue", lambda task, *a, **k: (queued.append((task, a)) or f"job-{len(queued)}") if task == "compute_job" else None)
    conn = records.connect(); records.init_db(conn)
    pin, _graph = _presets(tmp_path, monkeypatch)
    _chain_workflow(tmp_path, monkeypatch, pin)
    sess = f"chainq-{uuid.uuid4().hex[:6]}"
    run_spec = presets.resolve_run(conn, schema_id=pin, params={})
    with conn.transaction():
        records.upsert_schema(conn, run_spec.schema_id, run_spec.field_defs)
    ds = records.create_dataset(conn, title="Demo chain (queued)", schema_id=pin, session_id=sess)["id"]; conn.commit()
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-chain"}).status_code == 200
    pdfs = {"mu": _pdf("mu"), "nu": _pdf("nu")}
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in pdfs.items()]
    rid = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json()["id"]
    for n, b in pdfs.items():                                    # the passes run inline (no queue for them here); the compute is queued
        assert c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": (f"{n}.pdf", b, "application/pdf")}, data={"api_key": "k", "model": "m"}).status_code == 200
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    s_by = {s["step_id"]: s for s in view["steps"]}
    assert view["status"] == "running" and view["current_step"] == "assemble", (view["status"], view["current_step"])
    assert s_by["assemble"]["status"] == "running" and s_by["assemble"]["jobs"]["queued"] == 2
    assert view["now"]["phase"] == "computing" and view["now"]["done"] == 0 and view["now"]["total"] == 2
    assert [q[0] for q in queued] == ["compute_job"] * 2 and {q[1][1] for q in queued} == {"assemble"} and {q[1][2] for q in queued} == {_sha(b) for b in pdfs.values()}
    assert c.get("/api/documents", headers=H, params={"dataset": ds}).json()["documents"] == []
    # a second poll dispatches nothing twice
    c.get(f"/api/workflow-runs/{rid}", headers=H)
    assert len(queued) == 2
    # the worker: one paper lands, the step still waits; the second lands, the step is done and the next compute is queued.
    # The costly part (the record, its quotes located, the pages) is prepared before the job is locked, outside any
    # transaction; a second attempt at the paper that lands it meanwhile wins, and the first, finding it done, writes nothing.
    import psycopg
    real_prepare, seen = passes.prepare_document, []

    def prepare_then_race(conn_, *a, **k):
        seen.append(conn_.info.transaction_status)
        out = real_prepare(conn_, *a, **k)
        if len(seen) == 1:
            seen.append(passes.run_compute(conn, *queued[0][1]))
        return out
    monkeypatch.setattr(passes, "prepare_document", prepare_then_race)
    r1 = passes.run_compute(conn, *queued[0][1])
    monkeypatch.setattr(passes, "prepare_document", real_prepare)
    assert seen[0] == seen[1] == psycopg.pq.TransactionStatus.IDLE, seen
    assert seen[2]["status"] == "done" and seen[2]["error"] is None, seen[2]
    assert r1["status"] == "skipped" and r1["why"] == "job done", r1
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    assert view["now"]["phase"] == "computing" and view["now"]["done"] == 1 and len(queued) == 2
    assert passes.run_compute(conn, *queued[1][1])["status"] == "done"
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    s_by = {s["step_id"]: s for s in view["steps"]}
    assert s_by["assemble"]["status"] == "done" and s_by["assemble"]["outputs"]["n_papers"] == 2
    assert view["current_step"] == "mark" and s_by["mark"]["jobs"]["queued"] == 2 and len(queued) == 4 and queued[2][1][1] == "mark"
    assert passes.run_compute(conn, *queued[2][1])["status"] == "done" and passes.run_compute(conn, *queued[3][1])["status"] == "done"
    assert passes.run_compute(conn, *queued[3][1])["status"] == "skipped"              # a job already done is left alone
    view = c.get(f"/api/workflow-runs/{rid}", headers=H).json()
    s_by = {s["step_id"]: s for s in view["steps"]}
    assert s_by["mark"]["status"] == "done" and s_by["mark"]["outputs"]["edited_records"] >= 2
    assert view["status"] == "done" and s_by["review"]["review"]["satisfied"] is False       # the review gate holds the release, as before
    assert view["progress"]["papers"] == {"done": 2, "total": 2}                              # papers, not (paper, step) jobs
    docs = c.get("/api/documents", headers=H, params={"dataset": ds}).json()["documents"]
    assert len(docs) == 2
    v = c.get(f"/api/documents/{docs[0]['document_id']}/view", headers=H).json()
    assert v["chain"]["kind"] == "compute" and all(r["field_values"]["sign"] == "+" for r in v["records"])
    # a retry of a failed paper: the compute job goes back to pending, not queued under the pass job
    run = workflows.get_run(conn, rid)
    sha = _sha(pdfs["mu"])
    job = next(j for j in workflows.jobs(conn, rid, "mark") if j["pdf_sha256"] == sha)
    with conn.transaction():
        conn.execute("UPDATE workflow_job SET status = 'failed', error = 'x' WHERE id = %s::uuid", (job["id"],))
        conn.execute("UPDATE workflow_run SET status = 'running' WHERE id = %s::uuid", (rid,))
    monkeypatch.setattr(worker, "enqueue", lambda task, *a, **k: "pass-job-1")
    out = passes.requeue_papers(conn, workflows.get_run(conn, rid), [job], api_key="k", model="m")
    assert out["retried"] == [sha]
    assert next(j for j in workflows.jobs(conn, rid, "mark") if j["pdf_sha256"] == sha)["status"] == "pending"
    conn.close()


def test_a_compute_step_keeps_a_few_papers_in_the_queue_and_a_job_the_queue_dropped_is_queued_again(tmp_path, monkeypatch) -> None:
    """The live chain queued 114 papers at once; the queue drops a job not started within the
    hour and the worker made some forty records an hour, so 74 papers sat "queued" with no job
    behind them — and the owner's page, polled every minute, kept the run looking busy, so the
    reconcile never asked the queue. Now a compute step keeps ``COMPUTE_IN_FLIGHT`` papers in
    the queue (each landing dispatches the next), the reconcile looks at a job that has waited
    long itself, and a compute job the queue no longer has goes back to pending and is queued
    again — up to ``COMPUTE_MAX_TRIES``, then it fails as before."""
    if not _db_ok():
        pytest.skip("no Postgres")
    from test_pipeline import _presets
    from paperlens import worker
    _force_local(monkeypatch)
    fake = _Fake(); monkeypatch.setattr(providers, "generate_json", fake)
    monkeypatch.setattr(workflows, "COMPUTE_IN_FLIGHT", 2)
    queued: list[tuple] = []
    monkeypatch.setattr(worker, "enqueue", lambda task, *a, **k: (queued.append((task, a)) or f"job-{len(queued)}") if task == "compute_job" else None)
    conn = records.connect(); records.init_db(conn)
    pin, _graph = _presets(tmp_path, monkeypatch)
    _chain_workflow(tmp_path, monkeypatch, pin)
    sess = f"chaincap-{uuid.uuid4().hex[:6]}"
    run_spec = presets.resolve_run(conn, schema_id=pin, params={})
    with conn.transaction():
        records.upsert_schema(conn, run_spec.schema_id, run_spec.field_defs)
    ds = records.create_dataset(conn, title="Demo chain (capped)", schema_id=pin, session_id=sess)["id"]; conn.commit()
    c, H = _client(sess)
    assert c.patch(f"/api/datasets/{ds}", headers=H, json={"workflow": "demo-chain"}).status_code == 200
    pdfs = {n: _pdf(n) for n in ("alpha", "beta", "gamma")}
    staged = [{"sha256": _sha(b), "filename": f"{n}.pdf"} for n, b in pdfs.items()]
    rid = c.post(f"/api/datasets/{ds}/workflow/runs", headers=H, json={"new_pdfs": staged}).json()["id"]
    for n, b in pdfs.items():
        assert c.post(f"/api/workflow-runs/{rid}/papers", headers=H, files={"pdf": (f"{n}.pdf", b, "application/pdf")}, data={"api_key": "k", "model": "m"}).status_code == 200
    counts = lambda: c.get(f"/api/workflow-runs/{rid}", headers=H).json()["steps"]                       # noqa: E731
    assemble = lambda: next(s for s in counts() if s["step_id"] == "assemble")["jobs"]                    # noqa: E731
    # three papers ready, two in the queue, the third waits as pending; polls dispatch nothing more
    assert len(queued) == 2 and assemble()["queued"] == 2 and assemble()["pending"] == 1
    assert len(queued) == 2
    # a paper lands: its job's advance dispatches the third
    assert passes.run_compute(conn, *queued[0][1])["status"] == "done"
    assert len(queued) == 3 and assemble()["queued"] == 2 and assemble()["done"] == 1
    # the queue drops the second paper's job (the hour's expiry): the job has waited long, the run was polled a minute ago
    lost = queued[1][1]
    conn.execute("UPDATE workflow_job SET updated_at = now() - interval '1 hour' WHERE run_id = %s::uuid AND step_id = 'assemble' AND pdf_sha256 = %s", (rid, lost[2]))
    conn.execute("UPDATE workflow_run SET updated_at = now() WHERE id = %s::uuid", (rid,)); conn.commit()
    cands = workflows.reconcile_candidates(conn)
    mine = [(r, j) for r, j in cands if r["id"] == rid]
    assert [j["pdf_sha256"] for _r, j in mine] == [lost[2]]                     # the fresh queued job is not asked about
    expired = {"job_id": mine[0][1]["job_id"], "status": "JobStatus.complete", "success": False, "error": "job expired"}
    assert workflows.apply_job_status(conn, mine[0][0], mine[0][1], expired) == "requeued"
    again = next(j for j in workflows.jobs(conn, rid, "assemble") if j["pdf_sha256"] == lost[2])
    assert again["status"] == "queued" and again["tries"] == 1 and again["job_id"] == f"job-{len(queued)}" and len(queued) == 4
    # the dropped job's paper runs once: the new job makes it, a late run of the old one is skipped
    assert passes.run_compute(conn, *queued[3][1])["status"] == "done"
    assert passes.run_compute(conn, *lost)["status"] == "skipped"
    # a job the queue keeps losing fails after COMPUTE_MAX_TRIES, as a lost job did before
    third = queued[2][1]
    conn.execute("UPDATE workflow_job SET tries = %s, updated_at = now() - interval '1 hour' WHERE run_id = %s::uuid AND step_id = 'assemble' AND pdf_sha256 = %s",
                 (workflows.COMPUTE_MAX_TRIES, rid, third[2])); conn.commit()
    run, job = next((r, j) for r, j in workflows.reconcile_candidates(conn) if r["id"] == rid)
    assert workflows.apply_job_status(conn, run, job, None) == "lost"
    gone = next(j for j in workflows.jobs(conn, rid, "assemble") if j["pdf_sha256"] == third[2])
    assert gone["status"] == "failed" and "the queue lost this job" in gone["error"] and f"after {workflows.COMPUTE_MAX_TRIES} tries" in gone["error"]
    # a job still waiting in the queue is left alone, whatever its age
    assert workflows.apply_job_status(conn, run, {**job, "status": "queued"}, {"status": "JobStatus.queued"}) == "waiting"
    conn.close()
