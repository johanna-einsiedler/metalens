// Personal-preset editor: create (no ?id), edit an owned preset (?id=<personal>), or
// duplicate a built-in preset into a personal one (?id=<file preset>). A preset is ONE
// declarative document (see paperlens/preset_spec.py): the author's task instructions,
// the declared paper / entry / sub-entry fields, evidence + confidence policy, and the
// review layout. The server validates it live, renders the full prompt (its generated
// sections included), and mints the schema id a run will use.
import { api } from "/static/api.js";
import { esc, renderMarkdown } from "/static/grammar.js";

const $ = (s) => document.querySelector(s);
const id = new URLSearchParams(location.search).get("id");
const body = $("#pe-body");
let LOADED = null;                 // the preset being edited/duplicated (or null for new)
let timer = null;

const STARTER = {
  format: 2,
  meta: { title: "My preset", tagline: "", description: "", mode: "extraction" },
  paper: { fields: [] },
  entries: {
    key: "records", label: "Record", cardinality: "many",
    fields: [
      { name: "outcome", type: "string", label: "Outcome", help: "what was measured", evidence: "row", confidence: "main" },
      { name: "effect", type: "number", label: "Effect", help: "the reported effect, as printed", evidence: "value", confidence: "main" },
      { name: "n", type: "integer", label: "N", help: "sample size", evidence: "value", confidence: "main" },
    ],
    children: [],
  },
  confidence: { levels: ["high", "medium", "low"], notes: true,
                groups: [{ id: "main", label: "Main values", scope: "entry" }] },
  display: { tabs: [{ id: "details", label: "Details", fields: ["outcome", "effect", "n"] }], triage: "low_confidence_first" },
};

async function init() {
  const me = await api.me();
  if (!me || !me.email) {
    body.innerHTML = '<p class="muted">Please <a href="/">sign in</a> to create presets.</p>';
    return;
  }
  if (id) {
    try { LOADED = await api.presetDetail(id); }
    catch (e) { body.innerHTML = `<p class="muted">Couldn’t load preset: ${esc(e.message)}</p>`; return; }
  }
  if (LOADED && LOADED.setup) {          // a saved setup has no prompt of its own — it is the base's values
    body.innerHTML = `<h2>${esc(LOADED.title)}</h2>`
      + `<p class="muted"><b>Saved setup</b> of <code>${esc(LOADED.base_preset_id)}</code> — a private set of parameter values, not a preset of its own. `
      + `Edit it in the MASEMiner builder: <a href="/extract">Process papers</a> → MASEMiner → <i>${esc(LOADED.title)}</i>, change the values and save under the same name.</p>`
      + `<pre style="white-space:pre-wrap">${esc(JSON.stringify(LOADED.params || {}, null, 2))}</pre>`;
    return;
  }
  render();
  validateSoon(0);
}

// A loaded preset is editable in place only if it's one of the user's DB presets.
const isOwned = () => !!(LOADED && LOADED.source === "personal");

// The spec split for editing: the prose (prompt.text) in its own textarea, everything
// else as JSON. Reassembled on validate / save.
function splitSpec(spec) {
  const s = JSON.parse(JSON.stringify(spec || STARTER));
  delete s.id;                                   // the server assigns / keeps the id
  const text = (s.prompt && s.prompt.text) || "";
  s.prompt = { ...(s.prompt || {}) }; delete s.prompt.text; delete s.prompt.file;
  return { text, rest: s };
}
function assemble() {
  const rest = JSON.parse($("#pe-spec").value);         // throws → shown by the caller
  const meta = { ...(rest.meta || {}) };
  meta.title = $("#pe-title").value.trim() || meta.title;
  meta.tagline = $("#pe-tagline").value.trim();
  meta.description = $("#pe-desc").value.trim();
  return { ...rest, format: 2, meta, prompt: { ...(rest.prompt || {}), text: $("#pe-instructions").value } };
}

function render() {
  const p = LOADED || {};
  const owned = isOwned();
  const { text, rest } = splitSpec(p.spec);
  const heading = !LOADED ? "New preset" : owned ? "Edit preset" : "Duplicate as a personal preset";
  const note = LOADED && !owned
    ? '<p class="muted" style="margin:-8px 0 14px">This is a built-in preset — saving creates your own editable copy.</p>'
    : (LOADED && p.legacy
        ? '<p class="muted" style="margin:-8px 0 14px">This preset predates the declarative format; its old prompt is kept verbatim as the instructions and its fields were inferred. Saving converts it.</p>' : "");
  body.innerHTML = `
    <h2 style="margin:0 0 4px">${esc(heading)}</h2>
    <p class="muted" style="margin:0 0 6px">One document defines the task: your instructions, the fields to extract (paper-level, per entry, per sub-entry), which of them need a citation, which the model rates its confidence on, and how the review page lays them out. The output schema, evidence rules and confidence rules of the prompt are <b>generated</b> from that declaration.</p>
    ${note}
    <div class="wf-field"><label>Title</label><input id="pe-title" value="${esc((p.spec && p.spec.meta && p.spec.meta.title) || p.title || "")}"/></div>
    <div class="wf-field"><label>Tagline</label><input id="pe-tagline" value="${esc((p.spec && p.spec.meta && p.spec.meta.tagline) || p.tagline || "")}"/></div>
    <div class="wf-field"><label>Description</label><textarea id="pe-desc" class="sf-ta" rows="2">${esc((p.spec && p.spec.meta && p.spec.meta.description) || p.description || "")}</textarea></div>
    <div class="wf-field"><label>Task instructions</label>
      <div class="sf-help">What to extract and how — the domain rules. Do <b>not</b> write the JSON schema or the evidence / confidence rules here: they are generated from the declaration below and appended. <code>\${name}</code> refers to a parameter declared in <code>prompt.params</code>.</div>
      <textarea id="pe-instructions" rows="12" class="sf-ta">${esc(text)}</textarea></div>
    <div class="wf-field"><label>Declaration <span class="muted">(JSON — paper / entries / confidence / display)</span></label>
      <div class="sf-help">Field: <code>{"name","type": string|text|integer|number|boolean|enum|multi|list|table, "options", "required", "evidence": value|row|table|none, "confidence": &lt;group id&gt;, "help"}</code>. Sub-entries go in <code>entries.children</code>; a group's <code>scope</code> is paper / entry / child; <code>display.tabs</code> place entry fields and child keys.</div>
      <textarea id="pe-spec" rows="22" class="sf-ta" spellcheck="false">${esc(JSON.stringify(rest, null, 2))}</textarea></div>
    <div id="pe-check" class="pe-check"></div>
    <details class="pe-preview"><summary>Rendered prompt preview <span class="muted" id="pe-plen"></span></summary><div id="pe-prompt" class="md-preview"></div></details>
    <div class="wf-field"><label>Visibility</label>
      <div class="radio-row">
        <label class="radio"><input type="radio" name="pe-vis" value="private" ${p.visibility !== "public" ? "checked" : ""}/> Private (only you)</label>
        <label class="radio"><input type="radio" name="pe-vis" value="public" ${p.visibility === "public" ? "checked" : ""}/> Public (anyone can use)</label>
      </div></div>
    <div class="wf-actions" style="justify-content:flex-start;flex-wrap:wrap">
      <button class="btn btn-primary" id="pe-save">${owned ? "Save changes" : "Create preset"}</button>
      ${owned ? '<button class="btn btn-ghost" id="pe-del">Delete</button>' : ""}
      <a class="btn btn-ghost" href="/projects">Cancel</a>
      <span id="pe-msg" class="muted" style="align-self:center"></span>
    </div>`;
  $("#pe-save").onclick = save;
  const del = $("#pe-del"); if (del) del.onclick = doDelete;
  ["pe-title", "pe-tagline", "pe-desc", "pe-instructions", "pe-spec"].forEach((i) => $("#" + i).addEventListener("input", () => validateSoon(400)));
}

function validateSoon(ms) { if (timer) clearTimeout(timer); timer = setTimeout(validateNow, ms); }

// Live feedback: every error at once (they block saving), warnings (they don't), the
// schema id this declaration hashes to, and the full generated prompt.
async function validateNow() {
  const box = $("#pe-check"); if (!box) return;
  let spec;
  try { spec = assemble(); }
  catch (e) { box.innerHTML = `<div class="pe-err">✗ Declaration is not valid JSON: ${esc(e.message)}</div>`; return; }
  let r;
  try { r = await api.validatePreset(spec); }
  catch (e) { box.innerHTML = `<div class="pe-err">✗ ${esc(e.message)}</div>`; return; }
  if (!r.ok) {
    box.innerHTML = `<div class="pe-err"><b>${r.errors.length} problem${r.errors.length === 1 ? "" : "s"}</b><ul>${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>`;
    $("#pe-save").disabled = true;
    return;
  }
  $("#pe-save").disabled = false;
  box.innerHTML = `<div class="pe-ok">✓ Valid · schema <code>${esc((r.schema_id || "").replace(/^draft@/, "…@"))}</code>`
    + ` · ${(r.prompt || "").length} chars of prompt</div>`
    + (r.warnings.length ? `<div class="pe-warn"><ul>${r.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>` : "");
  $("#pe-prompt").innerHTML = renderMarkdown(r.prompt || "");
  $("#pe-plen").textContent = `· ${(r.prompt || "").length} characters`;
}

async function save() {
  const msg = $("#pe-msg"); msg.textContent = "";
  let spec;
  try { spec = assemble(); }
  catch (e) { msg.textContent = "✗ Declaration JSON: " + e.message; return; }
  if (!spec.meta.title) { msg.textContent = "✗ Title is required."; return; }
  const visibility = document.querySelector('input[name="pe-vis"]:checked').value;
  $("#pe-save").disabled = true;
  try {
    if (isOwned()) await api.updatePreset(id, { spec, visibility });
    else await api.createPreset({ spec, visibility });            // new, or a duplicate of a built-in
    location.href = "/projects";
  } catch (e) {
    const d = e && e.detail;
    msg.textContent = "✗ " + (d && d.errors ? d.errors.join(" · ") : e.message);
    $("#pe-save").disabled = false;
  }
}

async function doDelete() {
  if (!confirm("Delete this preset? Datasets already built with it keep working.")) return;
  try { await api.deletePreset(id); location.href = "/projects"; }
  catch (e) { $("#pe-msg").textContent = "✗ " + e.message; }
}

init();
