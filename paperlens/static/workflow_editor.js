// /workflow-editor — a workflow of your own: new, one of yours (?id=<mine>), or a bundled one
// copied (?id=<bundled>). A workflow is one document (docs/WORKFLOWS.md): steps of three
// families a person composes — Extract (a preset per paper), Check (a registered check, or
// rules in the workflow's own words), Review (where a person looks before a release) — and the
// Release at the end. The form writes the document; the server validates it live (every
// problem at once, placed under the step it names) and draws its contract; the diagram follows.
import { api } from "/static/api.js";
import { flowSvg } from "/static/flow.js";
import { esc } from "/static/grammar.js";
import { modelChoice, renderModelPanel } from "/static/modelpanel.js";

const $ = (s) => document.querySelector(s);
const body = $("#we-body");
let ID = null, LOADED = null, DOC = null, GRAMMAR = null, ME = null, timer = null, LAST = null;
let CFG = null, MODELS = {}, PROPOSAL = null, PROPOSING = false;     // the proposer: the model config, the last proposal
const PARTS = { presets: [], checks: [], templates: [] };
const IDENTITY = ["title", "doi", "year", "journal", "authors"];
const FAMILY = { extract: "Extract", check: "Check", review: "Review", release: "Release" };
const PURPOSE_HELP = {
  audit: "a first look at a model step: every paper or a draw; the agreement a person had with the model feeds the preset's stamp",
  routed: "the papers a rule sends on — the uncertain ones, named by `where`; never counted in the stamp",
  monitor: "a draw of each run's new papers that extends the stamp and holds nothing",
  calibrate: "two or more coders on the same units, so the coders' own agreement is known",
  adjudicate: "the units an earlier coders' review disagreed on, settled by a further verdict",
};
const PURPOSE_DEFAULTS = {
  audit: { mode: "sample", unit: "paper", n: 20, blocks: "release" },
  routed: { mode: "all", unit: "paper", where: { "paper_metadata.routed": true }, blocks: "release" },
  monitor: { mode: "sample", unit: "paper", n: 10, new_only: true, blocks: "none" },
  calibrate: { mode: "sample", unit: "paper", n: 20, coders: 2, blocks: "release" },
  adjudicate: { mode: "all", unit: "paper", coders: 1, blocks: "release" },
};

// ── the document ─────────────────────────────────────────────────────────────
function starter() {
  const p = PARTS.presets.find((x) => !x.personal && x.mode !== "import") || PARTS.presets[0] || {};
  return { format: 1, id: "my-workflow", meta: { title: "My workflow", tagline: "", description: "" }, steps: [
    { id: "extract", kind: "extract", label: "Extraction", preset: p.schema_id || "", source: p.mode === "import" ? "supplied" : "live" },
    { id: "contract", kind: "check", label: "Contract check", check: "schema", over: ["extract"], on_fail: "note", severity: "error" },
    { id: "audit", kind: "review", label: "Audit", over: ["extract"], mode: "sample", unit: "paper", n: 20, blocks: "release" },
    { id: "release", kind: "release", label: "Release", gate: ["extract", "audit"] } ] };
}
const clone = (x) => JSON.parse(JSON.stringify(x));
const steps = () => (DOC.steps || []);
const presetOf = (pin) => PARTS.presets.find((p) => p.schema_id === pin) || PARTS.presets.find((p) => p.preset_id === String(pin || "").split("@")[0]) || null;
const specOf = (pin) => (presetOf(pin) || {}).spec || null;
const stepPreset = (st) => st && (st.kind === "extract" || st.kind === "agent" || (st.kind === "compute" && st.preset)) ? st.preset : null;
const presetOfStep = (sid) => stepPreset(steps().find((s) => s.id === sid));
const scalar = (f) => f && f.type !== "table";
function fieldsFor(pin, unit) {                      // the fields a picker offers for a step over `pin`: paper, scalar entry, or a table's columns
  const spec = specOf(pin) || {};
  if (unit === "paper") return [...IDENTITY, ...((spec.paper || {}).fields || []).filter(scalar).map((f) => f.name)];
  if (unit === "entry") return (((spec.entries || {}).fields) || []).filter(scalar).map((f) => f.name);
  if (unit === "asks") return [...((((spec.entries || {}).fields) || []).map((f) => f.name)), ...((((spec.entries || {}).children) || []).map((c) => c.key))];
  return [];
}
const tablesFor = (pin) => (((specOf(pin) || {}).paper || {}).fields || []).filter((f) => f.type === "table").map((f) => f.name);

// ── rendering ────────────────────────────────────────────────────────────────
const opt = (v, label, cur) => `<option value="${esc(v)}"${String(cur) === String(v) ? " selected" : ""}>${esc(label == null ? v : label)}</option>`;
const sel = (k, options, cur, extra = "") => `<select data-k="${k}" ${extra}>${options.map((o) => (Array.isArray(o) ? opt(o[0], o[1], cur) : opt(o, o, cur))).join("")}</select>`;
const inp = (k, v, type = "str", extra = "") => `<input data-k="${k}" data-t="${type}" value="${esc(v == null ? "" : Array.isArray(v) ? v.join(", ") : v)}" ${extra}/>`;
const chk = (k, v) => `<label class="radio"><input type="checkbox" data-k="${k}" data-t="bool"${v ? " checked" : ""}/> ${k.replace("_", " ")}</label>`;
const ta = (k, v, rows = 4) => `<textarea data-k="${k}" data-t="json" rows="${rows}" class="sf-ta" spellcheck="false">${esc(v == null ? "" : JSON.stringify(v, null, 2))}</textarea>`;
const multi = (k, options, cur) => `<select data-k="${k}" data-t="list" multiple size="${Math.min(5, Math.max(2, options.length))}">${options.map((o) => opt(o, o, undefined)).join("")}</select>`
  .replace(/<option value="([^"]*)"/g, (m, v) => ((cur || []).includes(v) ? `${m} selected` : m));
const field = (label, html, help) => `<div class="we-f"><label>${esc(label)}</label>${html}${help ? `<div class="sf-help">${help}</div>` : ""}</div>`;
const earlier = (i, kinds) => steps().slice(0, i).filter((s) => kinds.includes(s.kind)).map((s) => s.id);

function presetOptions(cur) {
  const mine = PARTS.presets.filter((p) => p.personal), shared = PARTS.presets.filter((p) => !p.personal);
  const o = (p) => [p.schema_id, `${p.title || p.preset_id} · ${p.preset_id}${p.mode === "import" ? " (import)" : ""}${p.personal ? " (mine)" : ""}`];
  const opts = [...shared.map(o), ...mine.map(o)];
  if (cur && !opts.some((x) => x[0] === cur)) opts.unshift([cur, `${cur} (not on this server)`]);
  return opts;
}

function stepForm(st, i) {
  const k = st.kind;
  if (k === "extract") {
    const p = presetOf(st.preset);
    return field("Preset", sel("preset", presetOptions(st.preset), st.preset), p ? `pinned to <code>${esc(p.schema_id)}</code>` : "")
      + field("Papers come in by", sel("source", [["live", "a model call per paper (live)"], ["supplied", "results uploaded (supplied)"]], st.source || (p && p.mode === "import" ? "supplied" : "live")), "an import preset is always supplied")
      + `<details class="we-adv"><summary>Advanced</summary>${field("Dataset role", inp("dataset", st.dataset), "a workflow over several presets: the role this step's dataset plays")}${field("Readings per paper (k)", inp("k", st.k || 1, "int", 'min="1" max="5" style="width:80px"'), "more than one needs a vote; see the docs")}</details>`;
  }
  if (k === "check") {
    const reg = PARTS.checks.find((c) => c.id === st.check) || {};
    const over = earlier(i, ["extract", "agent", "compute", "pass"]);
    const params = { ...Object.fromEntries(Object.entries(reg.params || {}).map(([n, d]) => [n, d.default])), ...(st.params || {}) };
    const applies = reg.applies_to || {};
    const appliesTo = applies.presets === "*" ? "any preset" : Array.isArray(applies.presets) ? `for ${applies.presets.join(", ")}` : applies.passes ? "over a chain's passes" : "";
    const rules = st.check === "rules";
    return field("Check", sel("check", PARTS.checks.filter((c) => !(c.applies_to || {}).passes).map((c) => [c.id, `${c.id} — ${c.label || ""}`]), st.check), esc(appliesTo))
      + field("Over", multi("over", over, st.over || []), "the step(s) it reads")
      + `<div class="we-row">${field("On a finding", sel("on_fail", GRAMMAR.on_fail, st.on_fail || "note"), "note it · flag the record for review · fail the run")}${field("At or above", sel("severity", GRAMMAR.severities, st.severity || "error"))}</div>`
      + (rules
        ? field("Rules", ta("params.rules", params.rules || [], 8), `a list of rules over the preset's fields — see "Rules" in the docs; <button type="button" class="btn btn-ghost btn-sm" data-suggest="${i}">suggest from the preset's fields</button>`)
        : Object.keys(reg.params || {}).length ? field("Parameters", ta("params", params, 4), `declared: ${Object.entries(reg.params).map(([n, d]) => `<code>${esc(n)}</code> (${esc(d.type)})`).join(", ")}`) : "");
  }
  if (k === "review") {
    const over = earlier(i, ["extract", "agent", "derive", "compute"]);
    const pin = presetOfStep((st.over || [])[0]);
    const unit = st.unit || "paper";
    const purposes = (GRAMMAR.review.purposes || []);
    const strat = unit === "row" ? [] : fieldsFor(pin, unit === "paper" ? "paper" : "entry");
    const tplOpts = PARTS.templates.filter((t) => !pin || t.for === String(pin).split("@")[0]).map((t) => [t.id, `${t.id} — ${t.title || ""}`]);
    return (purposes.length ? field("Purpose", sel("purpose", [["", "— none —"], ...purposes.map((p) => [p, p])], st.purpose || ""), esc(PURPOSE_HELP[st.purpose] || "what the review is for; choosing one fills in its usual settings")) : "")
      + field("Over", multi("over", over, st.over || []), "the data step(s) the review reads")
      + `<div class="we-row">${field("Mode", sel("mode", [["all", "every unit"], ["sample", "a random draw"]], st.mode || "all"))}${field("Unit", sel("unit", [["paper", "papers"], ["entry", "entries"], ["row", "rows of a paper table"]], unit))}${field("Holds", sel("blocks", GRAMMAR.review.blocks.map((b) => [b, b === "next" ? "the run" : b === "release" ? "the release" : "nothing"]), st.blocks || "release"))}</div>`
      + (st.mode === "sample" && unit !== "row" ? `<div class="we-row">${field("n", inp("n", st.n, "int", 'min="1" style="width:90px"'), "a count…")}${field("share", inp("share", st.share, "num", 'min="0" max="1" step="0.05" style="width:90px"'), "…or a fraction")}${field("seed", inp("seed", st.seed), "optional")}</div>`
        + field("Stratify by", multi("stratify", strat, st.stratify || []), "fields of the unit") : "")
      + (unit === "row" ? `<div class="we-row">${field("Table", sel("table", tablesFor(pin).map((t) => [t, t]), st.table || ""), "a paper table of the preset")}${field("Rows per paper", inp("per_paper", st.per_paper || 1, "int", 'min="1" style="width:90px"'), "for a draw")}</div>` : "")
      + `<div class="we-row">${field("Coders per paper", inp("coders", st.coders, "int", 'min="1" style="width:90px"'), "leave empty: the owner reviews")}${field("Template", sel("template", [["", "— none —"], ...tplOpts], st.template || ""), "the questions the coder answers")}${field("", chk("new_only", st.new_only !== false), "only the run's new papers")}</div>`
      + field("Where", ta("where", st.where || null, 2), `the papers (or rows) the review keeps, e.g. <code>{"paper_metadata.routed": true}</code>; empty for all`)
      + field("Asks", ta("asks", st.asks || null, 2), `per step read, the entry fields to verify: <code>{"${esc((st.over || [])[0] || "extract")}": [${fieldsFor(pin, "asks").slice(0, 3).map((f) => `"${esc(f)}"`).join(", ")}…]}</code>; empty for every field`);
  }
  if (k === "release") {
    return field("Gate", multi("gate", earlier(i, ["extract", "agent", "derive", "compute", "review"]), st.gate || []), "the steps that must be fresh — and the reviews done — before a release");
  }
  return field("Step (JSON)", ta("$step", st, 8), `a <code>${esc(k)}</code> step: edited as a document (the chains' passes and computes are registered code)`);
}

function stepCard(st, i, errs) {
  const fam = FAMILY[st.kind] || st.kind;
  const fixed = st.kind === "release";
  return `<div class="we-step we-${esc(st.kind)}" data-i="${i}">
    <div class="we-head"><span class="badge we-fam">${esc(fam)}</span>
      <input class="we-id" data-k="id" data-t="str" value="${esc(st.id)}" title="the step's id (a-z, 0-9, _ )" ${fixed ? "readonly" : ""}/>
      <input class="we-label" data-k="label" data-t="str" value="${esc(st.label || "")}" placeholder="label"/>
      <span class="we-tools">${fixed ? "" : `<button type="button" title="move up" data-move="-1">↑</button><button type="button" title="move down" data-move="1">↓</button><button type="button" title="remove" data-remove="1">✕</button>`}</span></div>
    <div class="we-body">${stepForm(st, i)}</div>
    ${errs.length ? `<ul class="we-errs">${errs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}</div>`;
}

// ── the proposer: a workflow from a question ──────────────────────────────────
function proposerHtml() {
  const p = PROPOSAL;
  const presetHtml = !p ? "" : p.preset.use
    ? `<p><b>Preset:</b> uses <a href="/preset?id=${encodeURIComponent(p.preset.use)}">${esc(p.preset.title || p.preset.use)}</a> <code>${esc(p.preset.schema_id || "")}</code></p>`
    : p.preset.new
      ? `<div class="wp-preset"><p><b>A new preset:</b> ${esc(p.preset.title || "")} <span class="muted">· ${esc(((p.preset.new.entries || {}).fields || []).length)} entry field${((p.preset.new.entries || {}).fields || []).length === 1 ? "" : "s"}${((p.preset.new.paper || {}).fields || []).length ? `, ${((p.preset.new.paper || {}).fields || []).length} paper field${((p.preset.new.paper || {}).fields || []).length === 1 ? "" : "s"}` : ""}</span>
          ${p.preset.saved ? ` · saved as <a href="/preset?id=${encodeURIComponent(p.preset.saved.id)}&edit=1">${esc(p.preset.saved.id)}</a>` : ` · <button type="button" class="btn btn-primary btn-sm" id="wp-save-preset">Save this preset as mine</button> <span class="muted">(then the workflow can pin it)</span>`}</p>
          <table class="ds-cc"><thead><tr><th>Field</th><th>Type</th><th>Help</th></tr></thead><tbody>${[...((p.preset.new.paper || {}).fields || []).map((f) => ({ ...f, name: `paper.${f.name}` })), ...(((p.preset.new.entries || {}).fields) || [])].map((f) => `<tr><td><code>${esc(f.name)}</code></td><td>${esc(f.type || "")}${f.options ? ` (${esc(f.options.map((o) => (typeof o === "string" ? o : o.value || o.label)).join(", "))})` : ""}</td><td>${esc(f.help || "")}</td></tr>`).join("")}</tbody></table>
          ${(p.preset.errors || []).length ? `<ul class="we-errs">${p.preset.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
          <details class="we-adv"><summary>The preset document (JSON)</summary><pre class="pc-default" style="max-height:320px">${esc(JSON.stringify(p.preset.new, null, 2))}</pre></details></div>`
      : "";
  const canUse = p && (p.preset.use || (p.preset.new && p.preset.saved));
  return `<details class="we-propose" id="wp" ${p ? "open" : (ID ? "" : "open")}><summary><b>Start from a question</b> <span class="muted">— say what you want from the papers; a model proposes a preset and the steps, you review and save</span></summary>
    <div class="we-row"><div class="we-f" style="flex:2"><label>What do you want to get out of the papers? <span class="muted">(one question per line)</span></label><textarea id="wp-questions" rows="3" class="sf-ta" placeholder="Does working with an AI assistant change task performance, and by how much?">${esc((PROPOSAL && PROPOSAL.questions || []).map((q) => q.text).join("\n"))}</textarea></div>
      <div class="we-f" style="flex:1"><label>Context <span class="muted">(field, the papers, what to leave out)</span></label><textarea id="wp-context" rows="3" class="sf-ta">${esc((PROPOSAL && PROPOSAL.context) || "")}</textarea></div></div>
    <div id="wp-model" class="cmp-model"></div>
    <div class="wf-actions" style="justify-content:flex-start;margin-top:8px"><button type="button" class="btn btn-primary" id="wp-go" ${PROPOSING ? "disabled" : ""}>${p ? "Propose again" : "Propose"}</button>
      ${p ? `<input id="wp-feedback" placeholder="feedback for a revision (free)" style="min-width:260px"/><button type="button" class="btn btn-ghost" id="wp-revise">Revise</button>` : ""}
      <span id="wp-msg" class="muted" style="align-self:center"></span></div>
    ${p ? `<div class="wp-result">
      ${presetHtml}
      ${(p.notes || []).length ? `<p class="muted"><b>The model notes:</b> ${p.notes.map(esc).join(" · ")}</p>` : ""}
      ${(p.unanswered || []).length ? `<p class="muted"><b>Not answerable from the papers:</b> ${p.unanswered.map((u) => `${esc(u.question)} — ${esc(u.reason || "")}`).join("; ")}</p>` : ""}
      ${(p.dropped || []).length ? `<p class="muted"><b>Left out:</b> ${p.dropped.map(esc).join("; ")}</p>` : ""}
      <p><b>Workflow:</b> ${(p.workflow.steps || []).map((s) => `<span class="kw">${esc(s.label || s.id)}</span>`).join(" → ")} <span class="muted">· ${p.cost ? `${p.cost.calls_per_paper} model call${p.cost.calls_per_paper === 1 ? "" : "s"} per paper` : ""}${p.proposal ? ` · ${esc(p.proposal.model)}${p.proposal.charged ? " · 1 credit used" : ""}` : ""}</span></p>
      ${(p.errors || []).length ? `<ul class="we-errs">${p.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
      <div class="wf-actions" style="justify-content:flex-start"><button type="button" class="btn btn-primary" id="wp-use" ${canUse ? "" : "disabled"} title="${canUse ? "put these steps into the editor below" : "save the proposed preset first"}">Use this workflow</button>
        ${p.preset.saved ? `<a class="btn btn-ghost" href="/extract?preset=${encodeURIComponent(p.preset.saved.id)}" title="run the preset on a few papers before a project">Try the preset on a few papers</a>` : ""}</div>
    </div>` : ""}
  </details>`;
}

function wireProposer() {
  const host = $("#wp-model");
  if (host && !host.children.length) renderModelPanel(host, { prefix: "wp", cfg: CFG, models: MODELS, creditsNote: "one credit per proposal, revisions are free",
    note: "The model sees your questions, the presets and checks available here and the grammar — never your papers. It proposes; nothing is saved until you do." });
  const go = $("#wp-go"); if (go) go.onclick = () => propose(null);
  const rv = $("#wp-revise"); if (rv) rv.onclick = () => propose(($("#wp-feedback") || {}).value || "");
  const sp = $("#wp-save-preset"); if (sp) sp.onclick = savePreset;
  const use = $("#wp-use"); if (use) use.onclick = useProposal;
}

async function propose(feedback) {
  if (PROPOSING) return;
  const qs = $("#wp-questions").value.split("\n").map((q) => q.trim()).filter(Boolean);
  const msg = $("#wp-msg");
  if (!qs.length) { msg.textContent = "Write at least one question first."; $("#wp-questions").focus(); return; }
  const choice = modelChoice("wp", { quiet: true });
  if (!choice) { msg.textContent = "A proposal needs a model: use a credit, your own API key or a local model."; return; }
  PROPOSING = true; $("#wp-go").disabled = true; msg.textContent = "Asking the model… this usually takes 10–40 seconds.";
  try {
    const revising = feedback != null && PROPOSAL;
    const r = await api.proposeWorkflow({ questions: qs, context: $("#wp-context").value.trim(), ...choice,
      ...(revising ? { previous: { preset: PROPOSAL.preset, workflow: PROPOSAL.workflow, notes: PROPOSAL.notes }, feedback } : {}) });
    if (!r.ok) { msg.textContent = `The proposal failed: ${r.error || "unknown error"}`; return; }
    PROPOSAL = { ...r, context: $("#wp-context").value.trim() };
    if (r.credits && CFG) CFG.credits = r.credits;
    msg.textContent = "";
  } catch (e) { msg.textContent = e.message; }
  finally { PROPOSING = false; }
  rerender();
}

async function savePreset() {
  const msg = $("#wp-msg"); msg.textContent = "Saving the preset…";
  try {
    const out = await api.createPreset({ spec: PROPOSAL.preset.new, visibility: "private" });
    const list = await api.presets();
    PARTS.presets = (list.presets || []).filter((x) => x.schema_id && x.spec);
    const row = PARTS.presets.find((x) => x.preset_id === out.id || x.id === out.id) || {};
    PROPOSAL.preset.saved = { id: out.id, schema_id: row.schema_id || out.schema_id };
    for (const st of PROPOSAL.workflow.steps || []) if (st.kind === "extract" && String(st.preset || "").split("@")[0] === (PROPOSAL.preset.new || {}).id) st.preset = PROPOSAL.preset.saved.schema_id;
    msg.textContent = "";
  } catch (e) { const errs = (e.detail && e.detail.errors) || []; msg.textContent = errs.length ? `the preset does not validate: ${errs.slice(0, 3).join("; ")}` : e.message; }
  rerender();
}

function useProposal() {
  DOC = clone(PROPOSAL.workflow); delete DOC.card;
  LOADED = null; ID = null;
  rerender(); validateSoon(0);
  const first = $(".we-step"); if (first) first.scrollIntoView({ behavior: "smooth", block: "start" });
}

function render(errsByStep = {}, rootErrs = [], warns = []) {
  const owned = !!(LOADED && LOADED.owned);
  const copying = !!(LOADED && !LOADED.personal);
  const heading = !LOADED ? "New workflow" : owned ? "Edit workflow" : copying ? "Copy a bundled workflow" : "Copy a shared workflow";
  body.innerHTML = `${!LOADED ? proposerHtml() : ""}<div class="ds-head" style="align-items:flex-start"><div><h2 style="margin:.2em 0 4px">${esc(heading)}</h2>
      <p class="muted" style="margin:0;max-width:760px">Steps of three families run in order on every paper: <b>Extract</b> (a preset), <b>Check</b> (a registered check, or rules in your own words), <b>Review</b> (where a person looks); the <b>Release</b> waits for them. The server checks the document as you type and draws its contract.</p></div></div>
    ${copying ? `<p class="muted" style="margin:6px 0 12px">Saving makes a workflow of your own with these steps; the bundled one stays as it is.</p>` : ""}
    <div class="we-row"><div class="we-f" style="flex:2"><label>Title</label>${inp("meta.title", (DOC.meta || {}).title)}</div><div class="we-f" style="flex:1"><label>Id</label>${inp("id", DOC.id, "str", LOADED && LOADED.personal && owned ? "readonly" : "")}<div class="sf-help">${LOADED && owned ? "fixed" : "a slug; the server adds a suffix so no bundled id is taken"}</div></div></div>
    <div class="we-f"><label>Tagline</label>${inp("meta.tagline", (DOC.meta || {}).tagline)}</div>
    <div class="we-f"><label>Description</label><textarea data-k="meta.description" data-t="str" rows="2" class="sf-ta">${esc((DOC.meta || {}).description || "")}</textarea></div>
    <div id="we-diagram" class="wf-flow">${flowSvg((LAST && LAST.spec && LAST.spec.steps) || steps())}</div>
    ${rootErrs.length ? `<div class="pe-err"><b>${rootErrs.length} problem${rootErrs.length === 1 ? "" : "s"}</b><ul>${rootErrs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : ""}
    <div id="we-steps">${steps().map((st, i) => stepCard(st, i, errsByStep[i] || [])).join("")}</div>
    <div class="we-add">Add a step: <button type="button" class="btn btn-ghost btn-sm" data-add="extract">＋ Extract</button> <button type="button" class="btn btn-ghost btn-sm" data-add="check">＋ Check</button> <button type="button" class="btn btn-ghost btn-sm" data-add="review">＋ Review</button></div>
    <div id="we-check" class="pe-check">${LAST ? statusHtml(LAST) : ""}</div>
    ${warns.length ? `<div class="pe-warn"><ul>${warns.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : ""}
    <details class="we-adv"><summary>The document (JSON)</summary><textarea id="we-json" rows="18" class="sf-ta" spellcheck="false">${esc(JSON.stringify(DOC, null, 2))}</textarea>
      <div style="margin-top:6px"><button type="button" class="btn btn-ghost btn-sm" id="we-apply">Apply the JSON</button> <span class="muted" id="we-json-msg"></span></div></details>
    <div class="we-f"><label>Visibility</label><div class="radio-row">
      <label class="radio"><input type="radio" name="we-vis" value="private" ${(LOADED && LOADED.visibility) !== "public" ? "checked" : ""}/> Private (only you)</label>
      <label class="radio"><input type="radio" name="we-vis" value="public" ${(LOADED && LOADED.visibility) === "public" ? "checked" : ""}/> Public (anyone may run it)</label></div></div>
    <div class="wf-actions" style="justify-content:flex-start;flex-wrap:wrap">
      <button class="btn btn-primary" id="we-save" ${LAST && !LAST.ok ? "disabled" : ""}>${owned ? "Save changes" : "Save as my workflow"}</button>
      ${owned ? '<button class="btn btn-ghost" id="we-del">Delete</button>' : ""}
      <a class="btn btn-ghost" href="${LOADED ? `/workflow?id=${encodeURIComponent(ID)}` : "/workflows"}">Cancel</a>
      <span id="we-msg" class="muted" style="align-self:center"></span></div>`;
  wire();
  wireProposer();
}

function statusHtml(r) {
  if (!r.ok) return `<div class="pe-err">✗ ${r.errors.length} problem${r.errors.length === 1 ? "" : "s"} — fix them to save</div>`;
  return `<div class="pe-ok">✓ Valid · contract <code>${esc(r.contract || "")}</code>${(r.later || []).length ? ` · <span class="muted">${esc(r.later.join("; "))}</span>` : ""}</div>`;
}

// ── the form → the document ──────────────────────────────────────────────────
function readValue(el) {
  const t = el.dataset.t || "str";
  if (el.tagName === "SELECT" && el.multiple) return Array.from(el.selectedOptions).map((o) => o.value);
  if (t === "bool") return el.checked;
  if (t === "int") return el.value.trim() === "" ? null : (Number.isFinite(+el.value) ? Math.trunc(+el.value) : el.value);
  if (t === "num") return el.value.trim() === "" ? null : (Number.isFinite(+el.value) ? +el.value : el.value);
  if (t === "list") return el.value.split(",").map((x) => x.trim()).filter(Boolean);
  if (t === "json") { const v = el.value.trim(); if (!v) return null; try { return JSON.parse(v); } catch { return { "$invalid": el.value }; } }
  return el.value;
}
function setPath(obj, path, v) {
  const ks = path.split("."); let o = obj;
  for (const k of ks.slice(0, -1)) { if (typeof o[k] !== "object" || o[k] === null) o[k] = {}; o = o[k]; }
  const last = ks[ks.length - 1];
  if (v === null || v === "" || (Array.isArray(v) && !v.length && !["over", "gate"].includes(last))) delete o[last]; else o[last] = v;
}
function readForm() {
  body.querySelectorAll("[data-k]").forEach((el) => { if (!el.closest(".we-step")) setPath(DOC, el.dataset.k, readValue(el)); });
  const next = [];
  body.querySelectorAll(".we-step").forEach((card) => {
    const i = +card.dataset.i; const st = clone(steps()[i] || {});
    const whole = card.querySelector('[data-k="$step"]');
    if (whole) { const v = readValue(whole); if (v && !v.$invalid) Object.assign(st, v); next.push(st); return; }
    card.querySelectorAll("[data-k]").forEach((el) => setPath(st, el.dataset.k, readValue(el)));
    if (st.params && !Object.keys(st.params).length) delete st.params;
    next.push(st);
  });
  DOC.steps = next;
  const js = $("#we-json"); if (js && document.activeElement !== js) js.value = JSON.stringify(DOC, null, 2);
}

// ── validation, live ─────────────────────────────────────────────────────────
function validateSoon(ms) { if (timer) clearTimeout(timer); timer = setTimeout(validateNow, ms); }
async function validateNow() {
  const box = $("#we-check"); if (!box) return;
  const bad = JSON.stringify(DOC).includes('"$invalid"');
  let r;
  if (bad) r = { ok: false, errors: ["a JSON field does not parse (see the step it belongs to)"], warnings: [] };
  else { try { r = await api.validateWorkflow(DOC); } catch (e) { r = { ok: false, errors: [e.message], warnings: [] }; } }
  LAST = r;
  const byStep = {}, root = [];
  for (const e of r.errors || []) { const m = e.match(/^\$\.steps\[(\d+)\]/); if (m) (byStep[+m[1]] = byStep[+m[1]] || []).push(e.replace(/^\$\.steps\[\d+\]\.?/, "")); else root.push(e); }
  body.querySelectorAll(".we-step").forEach((card) => {
    const i = +card.dataset.i; const old = card.querySelector(".we-errs"); if (old) old.remove();
    const errs = byStep[i] || [];
    if (errs.length) card.insertAdjacentHTML("beforeend", `<ul class="we-errs">${errs.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`);
  });
  box.innerHTML = (root.length ? `<div class="pe-err"><b>${root.length} problem${root.length === 1 ? "" : "s"}</b><ul>${root.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : "") + statusHtml(r)
    + ((r.warnings || []).length ? `<div class="pe-warn"><ul>${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "");
  const dia = $("#we-diagram"); if (dia) dia.innerHTML = flowSvg((r.spec && r.spec.steps) || steps());
  const save = $("#we-save"); if (save) save.disabled = !r.ok;
}

// ── wiring ───────────────────────────────────────────────────────────────────
function wire() {
  body.querySelectorAll("[data-k]").forEach((el) => el.addEventListener("input", () => { readForm(); validateSoon(400); }));
  body.querySelectorAll("select[data-k]").forEach((el) => el.addEventListener("change", () => {
    readForm();
    const card = el.closest(".we-step"); const k = el.dataset.k;
    if (card && ["preset", "over", "mode", "unit", "check", "purpose"].includes(k)) {         // the form's shape depends on it
      const i = +card.dataset.i, st = steps()[i];
      if (k === "preset") { const p = presetOf(st.preset); if (p) st.source = p.mode === "import" ? "supplied" : "live"; }
      if (k === "purpose" && st.purpose && PURPOSE_DEFAULTS[st.purpose]) Object.assign(st, PURPOSE_DEFAULTS[st.purpose]);
      if (k === "check" && st.check === "rules") st.params = { rules: (st.params || {}).rules || [] };
      rerender();
    }
    validateSoon(0);
  }));
  body.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => {
    readForm();
    const kind = b.dataset.add, n = steps().filter((s) => s.kind === kind).length + 1, id = `${kind}_${n}`;
    const first = steps().find((s) => s.kind === "extract");
    const st = kind === "extract" ? { id, kind, label: `Extraction ${n}`, preset: (PARTS.presets[0] || {}).schema_id || "", source: "live" }
      : kind === "check" ? { id, kind, label: `Check ${n}`, check: "schema", over: first ? [first.id] : [], on_fail: "note", severity: "error" }
      : { id, kind, label: `Review ${n}`, over: first ? [first.id] : [], mode: "all", unit: "paper", blocks: "release" };
    const rel = steps().findIndex((s) => s.kind === "release");
    if (rel >= 0) DOC.steps.splice(rel, 0, st); else DOC.steps.push(st);
    rerender(); validateSoon(0);
  }));
  body.querySelectorAll("[data-move]").forEach((b) => (b.onclick = () => {
    readForm();
    const i = +b.closest(".we-step").dataset.i, j = i + Number(b.dataset.move);
    if (j < 0 || j >= steps().length || steps()[j].kind === "release") return;
    [DOC.steps[i], DOC.steps[j]] = [DOC.steps[j], DOC.steps[i]]; rerender(); validateSoon(0);
  }));
  body.querySelectorAll("[data-remove]").forEach((b) => (b.onclick = () => { readForm(); DOC.steps.splice(+b.closest(".we-step").dataset.i, 1); rerender(); validateSoon(0); }));
  body.querySelectorAll("[data-suggest]").forEach((b) => (b.onclick = async () => {
    readForm();
    const st = steps()[+b.dataset.suggest]; const pin = presetOfStep((st.over || [])[0]);
    if (!pin) { alert("Choose the step the check reads first."); return; }
    try {
      const r = await api.presetRules(String(pin).split("@")[0]);
      const have = new Set(((st.params || {}).rules || []).map((x) => x.code));
      st.params = { rules: [...((st.params || {}).rules || []), ...(r.rules || []).filter((x) => !have.has(x.code))] };
      if (!(r.rules || []).length) alert("Nothing its field names imply beyond the contract; write a rule by hand.");
      rerender(); validateSoon(0);
    } catch (e) { alert(e.message); }
  }));
  const apply = $("#we-apply");
  if (apply) apply.onclick = () => {
    try { DOC = JSON.parse($("#we-json").value); $("#we-json-msg").textContent = ""; rerender(); validateSoon(0); }
    catch (e) { $("#we-json-msg").textContent = `not valid JSON: ${e.message}`; }
  };
  $("#we-save").onclick = save;
  const del = $("#we-del"); if (del) del.onclick = doDelete;
}
function rerender() {
  const open = $("#we-json") && $("#we-json").closest("details") && $("#we-json").closest("details").open;
  render();
  if (open) $("#we-json").closest("details").open = true;
}

// ── save / delete ────────────────────────────────────────────────────────────
async function save() {
  readForm();
  const msg = $("#we-msg"); msg.textContent = "Saving…";
  const visibility = (body.querySelector('input[name="we-vis"]:checked') || {}).value || "private";
  try {
    const owned = !!(LOADED && LOADED.owned);
    const out = owned ? await api.updateWorkflow(ID, { spec: DOC, visibility }) : await api.createWorkflow({ spec: DOC, visibility });
    location.href = `/workflow?id=${encodeURIComponent(out.id)}`;
  } catch (e) {
    const errs = (e.detail && e.detail.errors) || (e.body && e.body.errors) || [];
    msg.textContent = errs.length ? `${errs.length} problem${errs.length === 1 ? "" : "s"}: ${errs.slice(0, 3).join("; ")}` : e.message;
    validateSoon(0);
  }
}
async function doDelete() {
  if (!confirm("Delete this workflow? Projects that run it keep it; only an unused workflow can go.")) return;
  try { await api.deleteWorkflow(ID); location.href = "/workflows"; } catch (e) { alert(e.message); }
}

// ── start ────────────────────────────────────────────────────────────────────
async function init() {
  ID = new URLSearchParams(location.search).get("id");
  try { ME = await api.me(); } catch { ME = null; }
  if (!ME || !(ME.email || ME.local_mode)) { body.innerHTML = '<p class="muted">Please <a href="/">sign in</a> to make a workflow.</p>'; return; }
  try {
    const [g, p, c, t, cfg, models] = await Promise.all([api.workflowGrammar(), api.presets(), api.checks(), api.templates(),
      api.extractionConfig().catch(() => null), api.models().then((m) => m.providers || {}).catch(() => ({}))]);
    GRAMMAR = g; PARTS.presets = (p.presets || []).filter((x) => x.schema_id && x.spec); PARTS.checks = c.checks || []; PARTS.templates = t.templates || [];
    CFG = cfg; MODELS = models;
  } catch (e) { body.innerHTML = `<p class="muted">Could not load the editor's parts: ${esc(e.message)}</p>`; return; }
  if (ID) {
    try { LOADED = await api.workflow(ID); }
    catch (e) { body.innerHTML = `<p class="muted">Couldn’t load this workflow: ${esc(e.message)}</p>`; return; }
    DOC = clone(LOADED.spec); delete DOC.card;
    if (!LOADED.personal) { DOC.id = `${DOC.id}-copy`; DOC.meta = { ...(DOC.meta || {}), title: `Copy of ${(DOC.meta || {}).title || DOC.id}` }; delete DOC.meta.hidden; delete DOC.meta.brands; delete DOC.meta.authors; delete DOC.meta.related; }
  } else DOC = starter();
  render();
  validateSoon(0);
}
init();
