// The EDIT MODE of /dashboard (?id=…&edit=1, or ?dataset=…&edit=1 for a new one): one screen.
// Questions and the model on top ("Ask"); below it the WHOLE dashboard in its real grid, drawn
// from the live data; click a block and a small inspector edits it (title, main slots; the rest
// under "More"). Loaded only for editors: readers never download this module.
// The server is the only validator: every structural edit goes through /api/dashboards/validate,
// which repairs, recomputes sufficiency from the data and tells which questions stay unanswered.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { getKey, setKey } from "/static/keys.js";
import { loadTable, OP_WORDS, setValueLabels, setColumnLabels, displayValue, MANY } from "/static/dash/table.js";
import { createEvidence } from "/static/dash/evidence.js";
import { renderBlock } from "/static/dash/blocks.js";
import { iconSvg } from "/static/dash/icons.js";
import { applyTheme, mountStylePanel } from "/static/dash/style.js";

const $ = (s, root = document) => root.querySelector(s);
const params = new URLSearchParams(location.search);
let DATASET_ID = params.get("dataset"), DASH = null, REG = null, CFG = null, MODELS = {};
let SPEC = { questions: [], blocks: [], unanswered: [], filters: {} }, REPORT = null, PROPOSAL = null;
let TITLE_TYPED = false;      // once the user has typed a title, a new proposal no longer replaces it
let QUESTIONS = [""], LOCKED = new Set(), BUSY = false;
let SELECTED = -1, MORE = false;      // the block in the inspector; whether its "More" section is open
const TABLES = new Map(), EVIDENCE = new Map();
const tplOf = (id) => REG.templates.find((t) => t.id === id);
const PROVIDER_LABEL = { openai: "OpenAI", google: "Google Gemini", anthropic: "Anthropic", deepseek: "DeepSeek", mistral: "Mistral" };

async function tableFor(unit) {
  if (!TABLES.has(unit)) { const t = await loadTable(DATASET_ID, unit); TABLES.set(unit, t); EVIDENCE.set(unit, createEvidence(t, $("#dash-panel"))); }
  return TABLES.get(unit);
}
const colLabelIn = (unit) => (name) => { const t = TABLES.get(unit); const c = t && t.col(name); return c ? c.label : name; };
function fits(col, slot) {
  if (!col.n) return false;
  if (slot.types && !slot.types.includes(col.type)) return false;
  if (slot.roles && !(col.roles || []).some((r) => slot.roles.includes(r))) return false;
  if (slot.max_distinct && col.distinct > slot.max_distinct) return false;
  return true;
}

// ── step 1: questions and how to get a first structure ───────────────────────
function renderQuestions() {
  $("#cmp-questions").innerHTML = QUESTIONS.map((q, i) => `<div class="cmp-q"><span class="cmp-qn">Q${i + 1}</span>`
    + `<input type="text" data-q="${i}" value="${esc(q)}" placeholder="${i ? "another question" : "e.g. Does working with an AI improve performance over working alone?"}"/>`
    + `${QUESTIONS.length > 1 ? `<button type="button" class="cmp-x" data-qx="${i}" title="remove">✕</button>` : ""}</div>`).join("")
    + `<button type="button" class="btn btn-ghost btn-sm" id="cmp-qadd">＋ Add a question</button>`;
  $("#cmp-questions").querySelectorAll("input[data-q]").forEach((el) => (el.oninput = () => { QUESTIONS[+el.dataset.q] = el.value; }));
  $("#cmp-questions").querySelectorAll("[data-qx]").forEach((el) => (el.onclick = () => { QUESTIONS.splice(+el.dataset.qx, 1); renderQuestions(); }));
  $("#cmp-qadd").onclick = () => { QUESTIONS.push(""); renderQuestions(); $("#cmp-questions").querySelectorAll("input[data-q]")[QUESTIONS.length - 1].focus(); };
}

function renderModelPanel() {
  const bal = CFG && CFG.credits ? CFG.credits.balance || 0 : 0;
  const credits = !!(CFG && CFG.logged_in && CFG.offered && bal > 0);
  const provs = Object.keys(MODELS);
  $("#cmp-model").innerHTML = `<div class="cmp-modes">`
    + (credits ? `<label class="cs-opt"><input type="radio" name="cmp-mode" value="credits" checked/> <span>Use a credit <b>(${bal} left)</b> <span class="muted">· ${esc(CFG.model || "")} · one credit per dashboard, revisions are free</span></span></label>` : "")
    + `<label class="cs-opt"><input type="radio" name="cmp-mode" value="own"${credits ? "" : " checked"}/> <span>My own API key</span></label>`
    + `<label class="cs-opt"><input type="radio" name="cmp-mode" value="local"/> <span>A local / self-hosted model</span></label></div>`
    + `<div id="cmp-own" class="cmp-fields"><div class="select-wrap"><select id="cmp-prov">${provs.map((p) => `<option value="${esc(p)}">${esc(PROVIDER_LABEL[p] || p)}</option>`).join("")}</select></div>`
    + `<div class="select-wrap"><select id="cmp-mdl"></select></div><input id="cmp-key" type="password" placeholder="your API key (used once, never stored)" autocomplete="off"/></div>`
    + `<div id="cmp-local" class="cmp-fields" hidden><input id="cmp-url" type="url" placeholder="server URL, e.g. https://models.example.org:8000"/>`
    + `<input id="cmp-lmdl" placeholder="model name"/></div>`
    + `<p class="muted cmp-note">The model sees your questions, the column names and a few sample values per column, never the PDFs. It proposes a structure; nothing is drawn until you approve it.</p>`;
  const fillModels = () => { const p = $("#cmp-prov").value; $("#cmp-mdl").innerHTML = (MODELS[p] || []).map((m) => `<option value="${esc(m.value)}">${esc(m.label)}</option>`).join(""); $("#cmp-key").value = getKey(p); };
  if (provs.length) { $("#cmp-prov").onchange = fillModels; fillModels(); }
  $("#cmp-key").oninput = () => setKey($("#cmp-prov").value, $("#cmp-key").value.trim());
  try { $("#cmp-url").value = localStorage.getItem("metalens_local_base_url") || ""; $("#cmp-lmdl").value = localStorage.getItem("metalens_local_model") || ""; } catch { /* no storage */ }
  const sync = () => { const m = $('input[name="cmp-mode"]:checked').value; $("#cmp-own").hidden = m !== "own"; $("#cmp-local").hidden = m !== "local"; };
  document.querySelectorAll('input[name="cmp-mode"]').forEach((r) => (r.onchange = sync)); sync();
}
function modelChoice() {
  const m = $('input[name="cmp-mode"]:checked').value;
  if (m === "credits") return { use_credits: true };
  if (m === "local") return { model: $("#cmp-lmdl").value.trim(), base_url: $("#cmp-url").value.trim(), api_key: "" };
  return { model: $("#cmp-mdl").value, api_key: $("#cmp-key").value.trim() };
}

async function propose(feedback) {
  if (BUSY) return;
  const qs = QUESTIONS.map((q) => q.trim()).filter(Boolean);
  if (!qs.length && !feedback) {
    status("Write at least one question first: the model plans the dashboard around your questions.", false, true);
    const q = $("#cmp-questions input[data-q]"); if (q) q.focus(); return;
  }
  const choice = modelChoice();
  if (!choice.use_credits && !choice.api_key && !choice.base_url) {
    const noCredits = CFG && CFG.logged_in && !((CFG.credits || {}).balance > 0);
    status(`${noCredits ? "You have no credits left. " : ""}A proposal needs a model: enter your own API key (or a local model server) on the left. The default dashboard below needs none.`, false, true);
    const k = $('input[name="cmp-mode"]:checked').value === "local" ? $("#cmp-url") : $("#cmp-key"); if (k) k.focus(); return;
  }
  BUSY = true; $("#cmp-propose").disabled = true;
  status(`Asking the model for a structure… this usually takes 10–40 seconds.${SPEC.blocks.length ? " The blocks below are replaced when it answers." : ""}`, true);
  try {
    const revising = !!feedback && SPEC.blocks.length > 0;
    const r = await api.proposeDashboard({ dataset_id: DATASET_ID, questions: qs, context: $("#cmp-context").value.trim(), ...choice, dashboard_id: DASH ? DASH.id : null,
      ...(revising ? { previous: SPEC, feedback, keep: [...LOCKED] } : {}) });
    if (!r.ok) { status(`The proposal failed: ${r.error || "unknown error"}`, false, true); showModelOutput(r, { failed: true }); return; }
    showModelOutput(r);
    SPEC = r.spec; REPORT = r.report; PROPOSAL = r.proposal;
    if (r.credits && CFG) CFG.credits = r.credits;
    status(`Proposed ${SPEC.blocks.length} block${SPEC.blocks.length === 1 ? "" : "s"} with ${r.proposal.model}${r.proposal.charged ? " · 1 credit used" : ""}. Review them below.`);
    SELECTED = -1; await renderReview(); $("#ed-ask").open = false; $("#dash-grid").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) { status(`The proposal failed: ${e.message}`, false, true); }
  finally { BUSY = false; $("#cmp-propose").disabled = false; }
}
async function useDefault() {
  status("Building the default dashboard…", true);
  const r = await api.validateDashboard(DATASET_ID, null);
  SPEC = { ...r.spec, context: $("#cmp-context").value.trim(), questions: QUESTIONS.map((q) => q.trim()).filter(Boolean).map((t, i) => ({ id: `q${i + 1}`, text: t })) };
  SELECTED = -1; await revalidate(); status("This is the default dashboard, built from the column types. Click a block to change it.");
  $("#ed-ask").open = false;
}
// What the model answered, verbatim: open after a failure (to tell a cut-off answer from a wrong
// one), folded away after a success (to debug, or to keep: the download holds the prompt, the
// answer and what the validator made of it). `r` = the propose response, or a stored proposal.
let LAST_OUTPUT = null;
function showModelOutput(r, { failed = false, stored = false } = {}) {
  const raw = r.raw ?? (r.proposal || {}).raw ?? "", model = r.model || (r.proposal || {}).model || "", attempts = r.attempts || (r.proposal || {}).attempts || 1;
  if (!raw && !failed) { $("#cmp-failure").innerHTML = ""; return; }
  const report = r.report || {}, dropped = report.dropped || [];
  LAST_OUTPUT = { model, attempts, created_at: new Date().toISOString(), dataset_id: DATASET_ID, questions: QUESTIONS.map((q) => q.trim()).filter(Boolean),
    context: $("#cmp-context").value.trim(), prompt: r.prompt || null, response: raw, report: stored ? null : report };
  $("#cmp-failure").innerHTML = `<details class="cmp-rep"${failed ? " open" : ""}><summary>${failed ? "What the model returned" : stored ? "Model output this dashboard was built from" : "Model output"}`
    + `${model ? ` (${esc(model)}${stored ? "" : `, ${attempts} attempt${attempts === 1 ? "" : "s"}`})` : ""}</summary>`
    + `<div class="cmp-out-bar"><button type="button" class="btn btn-ghost btn-sm" id="cmp-out-dl">⬇ Download (.json)</button>`
    + `<button type="button" class="btn btn-ghost btn-sm" id="cmp-out-copy">Copy the answer</button>`
    + `<span class="muted">${r.prompt ? "the file holds the prompt, the answer and the validator’s report" : "the prompt is not stored; the file holds the answer"}</span></div>`
    + (dropped.length ? `<ul>${dropped.map((d) => `<li>block ${d.index + 1}: ${esc(d.reason)}</li>`).join("")}</ul>` : "")
    + `<pre class="raw-json">${esc(raw || "(empty response)")}</pre>`
    + (r.prompt ? `<details class="cmp-rep"><summary>The prompt that was sent</summary><pre class="raw-json">${esc(r.prompt)}</pre></details>` : "") + `</details>`;
  $("#cmp-out-dl").onclick = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(LAST_OUTPUT, null, 2)], { type: "application/json" }));
    a.download = `dashboard-proposal-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-")}.json`; a.click(); URL.revokeObjectURL(a.href);
  };
  $("#cmp-out-copy").onclick = async () => { try { await navigator.clipboard.writeText(raw); $("#cmp-out-copy").textContent = "Copied"; } catch { /* clipboard blocked */ } };
}
// what is going on, next to the two buttons; `stop` = the click could not do anything (say so
// loudly and show where: a missing question, a missing key), so it never "looks like nothing"
function status(msg, busy, stop) {
  const el = $("#cmp-status");
  el.className = stop ? "cmp-stop" : "muted";
  el.innerHTML = `${busy ? '<span class="spin"></span> ' : ""}${esc(msg)}`;
  if (stop || busy) el.scrollIntoView({ behavior: "smooth", block: "center" });
}

// ── step 2: review and edit ──────────────────────────────────────────────────
async function revalidate() {
  const r = await api.validateDashboard(DATASET_ID, SPEC);
  SPEC = r.spec; REPORT = r.report;
  await renderReview();
}

function slotEditor(block, tpl, slot, table) {
  const cur = (block.bindings || {})[slot.id] || {};
  const cols = table.columns.filter((c) => fits(c, slot) || c.name === cur.column || (cur.columns || []).includes(c.name));
  const opt = (c, sel) => `<option value="${esc(c.name)}"${sel ? " selected" : ""}>${esc(c.label)}${c.derived ? " (computed)" : ""}</option>`;
  let html = `<label class="cmp-slot"><span class="cmp-slot-l">${esc(slot.label)}${slot.required ? " *" : ""}</span>`;
  if (slot.multi) {
    const chosen = cur.columns || [];
    html += `<span class="cmp-chips">${chosen.map((n) => `<span class="cmp-chip">${esc(colLabelIn(block.unit)(n))}<button type="button" data-act="unchip" data-slot="${esc(slot.id)}" data-col="${esc(n)}">✕</button></span>`).join("")}`
      + `<select data-act="chip" data-slot="${esc(slot.id)}"><option value="">＋ add…</option>${cols.filter((c) => !chosen.includes(c.name)).map((c) => opt(c)).join("")}</select></span>`;
  } else {
    html += `<select data-act="bind" data-slot="${esc(slot.id)}"><option value="">${slot.required ? "choose…" : "—"}</option>${cols.map((c) => opt(c, c.name === cur.column)).join("")}</select>`;
    if (slot.agg && cur.column) html += `<select data-act="agg" data-slot="${esc(slot.id)}" title="how rows are combined">${REG.aggs.filter((a) => a !== "count").map((a) => `<option${a === (cur.agg || slot.agg) ? " selected" : ""}>${a}</option>`).join("")}</select>`;
  }
  return html + (slot.help ? `<span class="cmp-help">${esc(slot.help)}</span>` : "") + `</label>`;
}

// what the PREVIEW of block i may change in place: a key number's caption, an option a hint offers
const editFor = (i) => ({
  title: (text) => { const b = SPEC.blocks[i]; b.title = text; b.origin = "user"; if (i === SELECTED) { const t = $('#dash-inspector [data-act="title"]'); if (t) t.value = text; } },
  note: async (text) => { const b = SPEC.blocks[i]; b.options = { ...(b.options || {}), note: text }; b.origin = "user"; await revalidate(); },
  option: async (id, value) => { const b = SPEC.blocks[i]; b.options = { ...(b.options || {}), [id]: value }; b.origin = "user"; await revalidate(); },
});
function optionEditor(block, o) {
  if (o.inline) return "";                                   // edited in the preview itself (a key number's caption)
  const v = (block.options || {})[o.id];
  const input = o.type === "boolean" ? `<input type="checkbox" data-act="opt" data-opt="${esc(o.id)}"${v ? " checked" : ""}/>`
    : `<input type="${o.type === "string" ? "text" : "number"}" ${o.type === "string" ? 'maxlength="120"' : 'step="any"'} data-act="opt" data-opt="${esc(o.id)}" value="${esc(v == null ? "" : String(v))}"/>`;
  return `<label class="cmp-slot cmp-opt"><span class="cmp-slot-l">${esc(o.label)}</span>${input}${o.help ? `<span class="cmp-help">${esc(o.help)}</span>` : ""}</label>`;
}

// The inline form for one new filter: a column with few distinct values offers them as
// checkboxes (with how many rows each has); a numeric column offers a condition.
function filterFormHtml(col, rows) {
  const numeric = col.type === "number" || col.type === "integer";
  const counts = new Map();
  const shown = new Map();                                   // coded value → how it is displayed
  for (const r of rows) { const v = r.raw(col.name); if (v != null && v !== "") { counts.set(String(v), (counts.get(String(v)) || 0) + 1); shown.set(String(v), String(r.get(col.name))); } }
  const few = counts.size > 0 && counts.size <= 30 && !(numeric && counts.size > 12);
  let body;
  if (few) {
    body = `<div class="ff-values">${[...counts.entries()].sort((a, b) => b[1] - a[1]).map(([v, n]) =>
      `<label><input type="checkbox" class="ff-val" value="${esc(v)}"/> ${esc(shown.get(v) || v)} <span class="muted">(${n})</span></label>`).join("")}</div>`;
  } else if (numeric) {
    body = `<select class="ff-op"><option value="gte">at least</option><option value="lte">at most</option><option value="between">between</option><option value="not_null">has a value</option></select>`
      + `<input class="ff-a" type="number" step="any" placeholder="${col.min != null ? col.min : ""}"/><input class="ff-b" type="number" step="any" placeholder="${col.max != null ? col.max : ""}" hidden/>`;
  } else {
    body = `<select class="ff-op"><option value="eq">is exactly</option><option value="not_null">has a value</option></select><input class="ff-a" type="text" placeholder="value"/>`;
  }
  return `<div class="cmp-fform" data-col="${esc(col.name)}" data-kind="${few ? "values" : numeric ? "number" : "text"}"><b>Keep rows where ${esc(col.label)}</b> ${few ? "is one of:" : ""}${body}`
    + `<span class="ff-btns"><button type="button" class="btn btn-primary btn-sm" data-act="fapply">Apply</button><button type="button" class="btn btn-ghost btn-sm" data-act="fcancel">Cancel</button></span></div>`;
}
function readFilterForm(form) {
  const column = form.dataset.col, kind = form.dataset.kind;
  if (kind === "values") {
    const vals = [...form.querySelectorAll(".ff-val:checked")].map((x) => x.value);
    return vals.length ? { column, op: "in", value: vals } : null;
  }
  const op = form.querySelector(".ff-op").value, a = form.querySelector(".ff-a").value.trim(), bEl = form.querySelector(".ff-b");
  if (op === "not_null") return { column, op };
  if (a === "") return null;
  if (kind === "text") return { column, op: "eq", value: a };
  if (op === "between") { const b = bEl.value.trim(); return b === "" ? null : { column, op, value: [+a, +b] }; }
  return { column, op, value: +a };
}

function filterEditor(block, table) {
  const fs = (block.transform || {}).filter || [];
  let fcol = null;                                           // the column of the chip being written
  const shownAs = (v) => (fcol ? displayValue(table, fcol, v) : v);
  return `<div class="cmp-slot"><span class="cmp-slot-l">Filters</span><span class="cmp-chips">`
    + fs.map((f, i) => { fcol = table.col(f.column); return `<span class="cmp-chip">${esc(colLabelIn(block.unit)(f.column))} ${esc(OP_WORDS[f.op] || f.op)}${f.op === "not_null" ? "" : " " + esc(Array.isArray(f.value) ? f.value.map(shownAs).join(f.op === "between" ? " and " : ", ") : String(shownAs(f.value)))}<button type="button" data-act="unfilter" data-i="${i}">✕</button></span>`; }).join("")
    + `<select data-act="fcol" title="keep only the rows that meet a condition; the block is then drawn from those rows"><option value="">＋ filter on…</option>${table.columns.filter((c) => c.n && (c.roles || []).length).map((c) => `<option value="${esc(c.name)}">${esc(c.label)}</option>`).join("")}</select></span></div>`;
}

async function renderReview() {
  await Promise.all([...new Set(SPEC.blocks.map((b) => b.unit))].map(tableFor));
  setValueLabels(TABLES, SPEC.value_labels); setColumnLabels(TABLES, SPEC.column_labels);
  const rep = REPORT || {};
  $("#cmp-report").innerHTML = ((rep.repairs || []).length || (rep.dropped || []).length
    ? `<details class="cmp-rep"><summary>${(rep.repairs || []).length} repair${(rep.repairs || []).length === 1 ? "" : "s"}, ${(rep.dropped || []).length} dropped</summary><ul>`
      + (rep.repairs || []).map((x) => `<li>${esc(x)}</li>`).join("") + (rep.dropped || []).map((x) => `<li>dropped block ${x.index + 1}: ${esc(x.reason)}</li>`).join("") + `</ul></details>` : "")
    + ((SPEC.unanswered || []).length && SPEC.blocks.length ? `<div class="dash-warn"><b>Not answered yet:</b><ul>${SPEC.unanswered.map((u) => {
      const q = SPEC.questions.find((x) => x.id === u.question); return `<li>${esc(q ? q.text : u.question)} <span class="muted">(${esc(u.reason || "")})</span></li>`; }).join("")}</ul></div>` : "");
  if (SELECTED >= SPEC.blocks.length) SELECTED = SPEC.blocks.length - 1;
  renderGrid(); renderInspector(); renderLabels(); renderAskState();
  // the title: what the user typed wins, else the one proposed with the blocks, else the saved one
  if (!TITLE_TYPED) {
    const fromModel = SPEC.title && PROPOSAL;
    $("#cmp-title").textContent = SPEC.title || (DASH ? DASH.title : "") || `${[...TABLES.values()][0].dataset.title || "Dataset"}: overview`;
    $("#cmp-title-src").textContent = fromModel ? "title proposed by the model · click it to change" : "click the title to change it";
  }
  SPEC.title = $("#cmp-title").textContent.trim();
}

// The whole dashboard, in the grid a reader will see, over the live data. A block is selected by
// clicking it; its title and a key number's caption are typed in place.
function renderGrid() {
  const grid = $("#dash-grid"); grid.innerHTML = ""; grid.classList.add("editing");
  let stats = null;
  SPEC.blocks.forEach((block, i) => {
    let host = grid;
    if (block.type === "stat") { if (!stats) { stats = document.createElement("div"); stats.className = "dash-stats"; grid.appendChild(stats); } host = stats; } else stats = null;
    const card = renderBlock(host, block, { tables: TABLES, evidence: EVIDENCE, questions: [], dashFilters: {}, edit: editFor(i) });
    if (!card) return;
    card.dataset.i = i; card.tabIndex = 0; card.classList.add("ed-block"); card.classList.toggle("selected", i === SELECTED);
    card.setAttribute("aria-label", `${block.title || "block"}: click to edit`);
    const pick = (e) => { if (e.target.closest("[contenteditable], button, a, .dash-mark, .cell-trace")) return; select(i); };
    card.addEventListener("click", pick);
    card.addEventListener("keydown", (e) => { if (e.key === "Enter" && e.target === card) select(i); });
  });
  const add = document.createElement("button");
  add.type = "button"; add.className = "ed-addtile"; add.innerHTML = "<span>＋</span> Add a block";
  add.onclick = () => { const g = $("#ed-gallery"); g.hidden = !g.hidden; if (!g.hidden) g.scrollIntoView({ behavior: "smooth", block: "nearest" }); };
  grid.appendChild(add);
  const gal = document.createElement("div"); gal.id = "ed-gallery"; gal.className = "ds-card cmp-more"; gal.hidden = true;
  gal.innerHTML = `<div class="ds-card-h">Add a block</div><div id="cmp-gallery" class="cmp-gallery">`
    + REG.templates.map((t) => `<button type="button" class="cmp-add" data-tpl="${esc(t.id)}" title="${esc(t.description)}">${iconSvg(t.icon, {})}<span>${esc(t.label)}</span></button>`).join("") + `</div>`;
  grid.appendChild(gal);
  gal.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-tpl]"); if (!b) return;
    SPEC.blocks.push({ template: b.dataset.tpl, title: tplOf(b.dataset.tpl).label, unit: [...TABLES.values()][0].units.find((u) => u.default).id, bindings: {}, origin: "user" });
    SELECTED = SPEC.blocks.length - 1; MORE = false; await revalidate();
    const el = $(`#dash-grid .ed-block[data-i="${SELECTED}"]`); if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  });
  $("#dash-empty").hidden = SPEC.blocks.length > 0;
}
function select(i) {
  SELECTED = i === SELECTED ? -1 : i; MORE = false;
  document.querySelectorAll("#dash-grid .ed-block").forEach((c) => c.classList.toggle("selected", +c.dataset.i === SELECTED));
  renderInspector();
}

// The inspector of the selected block: what most edits need (kind of block, the required slots,
// colour); everything else under "More". It is a .cmp-card with data-i, so the block actions
// below find their block the same way they always did.
function renderInspector() {
  const host = $("#dash-inspector"), block = SPEC.blocks[SELECTED];
  document.body.classList.toggle("ed-inspecting", !!block);
  if (!block) { host.hidden = true; host.innerHTML = ""; return; }
  const i = SELECTED, tpl = tplOf(block.template), table = TABLES.get(block.unit), suff = block.sufficiency || {}, units = [...TABLES.values()][0].units;
  const main = tpl.slots.filter((sl) => sl.required || sl.id === "color"), rest = tpl.slots.filter((sl) => !main.includes(sl));
  const nMore = rest.length + (tpl.options || []).filter((o) => !o.inline).length;
  host.hidden = false;
  host.innerHTML = `<article class="cmp-card ed-inspector${suff.status === "insufficient" ? " bad" : ""}" data-i="${i}">`
    + `<div class="ed-insp-h"><b>${esc(block.type === "stat" ? "Key number" : block.type === "table" ? "Table" : "Figure")}</b>`
    + `<span class="cmp-badge s-${suff.status === "insufficient" ? "insufficient" : "ok"}" title="${esc(suff.reason || "")}">${suff.status === "insufficient" ? "not enough data" : `${suff.n_rows} rows`}</span>`
    + `<button type="button" class="tp-x" id="ed-insp-x" title="close" aria-label="close">✕</button></div>`
    + (suff.status === "insufficient" && suff.reason ? `<p class="cmp-why">${esc(suff.reason)}</p>` : "")
    + `<label class="cmp-slot"><span class="cmp-slot-l">Title</span><input class="cmp-title" data-act="title" value="${esc(block.title || "")}" placeholder="title"/></label>`
    + `<label class="cmp-slot"><span class="cmp-slot-l">Kind</span><select data-act="template" title="the kind of block">${REG.templates.map((t) => `<option value="${esc(t.id)}"${t.id === tpl.id ? " selected" : ""}>${esc(t.label)}</option>`).join("")}</select></label>`
    + main.map((sl) => slotEditor(block, tpl, sl, table)).join("")
    + `<details class="ed-more"${MORE ? " open" : ""}><summary>More <span class="muted">· ${nMore ? `${nMore} more setting${nMore === 1 ? "" : "s"}, ` : ""}filters, row unit, order</span></summary>`
    + rest.map((sl) => slotEditor(block, tpl, sl, table)).join("") + (tpl.options || []).map((o) => optionEditor(block, o)).join("") + filterEditor(block, table)
    + `<label class="cmp-unit" title="What this block counts and plots. The data is nested: an ${esc(units[0].label.toLowerCase())} can hold several of the next level, and so on. Pick the level your question is about."><span>One row is one</span>`
    + `<select data-act="unit">${units.map((u) => `<option value="${esc(u.id)}"${u.id === block.unit ? " selected" : ""}>${esc(u.label.toLowerCase())} (${u.n ?? "?"} rows)</option>`).join("")}</select></label>`
    + (SPEC.questions.length ? `<div class="cmp-answers"><span class="muted">answers</span>${SPEC.questions.map((q) => `<label title="${esc(q.text)}"><input type="checkbox" data-act="answer" value="${esc(q.id)}"${(block.answers || []).includes(q.id) ? " checked" : ""}/> ${esc(q.id.toUpperCase())}</label>`).join("")}</div>` : "")
    + `<textarea data-act="message" rows="2" placeholder="Note to yourself: what should a reader take from this block? (not shown to readers)">${esc(block.main_message || "")}</textarea>`
    + ((block.caveats || []).length ? `<ul class="dash-caveats">${block.caveats.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : "")
    + `<div class="cmp-actions"><button type="button" data-act="up" title="move earlier"${i ? "" : " disabled"}>↑</button><button type="button" data-act="down" title="move later"${i < SPEC.blocks.length - 1 ? "" : " disabled"}>↓</button>`
    + `<button type="button" data-act="dup" title="duplicate">⧉</button><button type="button" data-act="lock" class="${LOCKED.has(block.id) ? "on" : ""}" title="keep this block when the model revises the proposal">${LOCKED.has(block.id) ? "🔒 kept" : "🔓 keep"}</button></div>`
    + `</details>`
    + `<div class="ed-insp-foot"><button type="button" class="btn btn-ghost btn-sm" data-act="remove">🗑 Remove block</button>`
    + `<span class="cmp-origin">${block.origin === "llm" ? "proposed by the model" : block.origin === "default" ? "default" : "yours"}</span></div></article>`;
  $("#ed-insp-x").onclick = () => select(SELECTED);
  $(".ed-more", host).addEventListener("toggle", (e) => { MORE = e.target.open; });
}

// The coded values of every categorical column the blocks use, each with the label it is shown
// under: the planner's suggestion, else the un-snaked code. Typing a label redraws the previews.
function labelTargets() {
  const out = new Map();                                     // column name → {col, table, values:Set}
  for (const b of SPEC.blocks) {
    const t = TABLES.get(b.unit); if (!t) continue;
    // columns that GROUP the marks (axis categories, colour, table groups); free-text names in
    // the label slot are left as the papers wrote them
    const names = Object.entries(b.bindings || {}).filter(([slot]) => slot !== "label")
      .flatMap(([, x]) => (x ? [x.column, ...(x.columns || [])] : [])).filter(Boolean);
    for (const n of new Set(names)) {
      const c = t.col(n);
      if (!c || n.startsWith("_") || !(c.roles || []).includes("dimension") || c.type === "number" || c.type === "integer") continue;
      const e = out.get(n) || { col: c, table: t, values: new Set() };
      for (const r of t.rows) { const v = r.raw(n); (Array.isArray(v) ? v : typeof v === "string" && MANY.has(c.type) ? v.split(", ") : v == null ? [] : [String(v)]).forEach((x) => x !== "" && e.values.add(String(x))); }
      if (e.values.size && e.values.size <= 40) out.set(n, e);
    }
  }
  return out;
}
// every column the blocks use, for renaming
function usedColumns() {
  const out = new Map();
  for (const b of SPEC.blocks) {
    const t = TABLES.get(b.unit); if (!t) continue;
    const names = [...Object.values(b.bindings || {}).flatMap((x) => (x ? [x.column, ...(x.columns || [])] : [])), ...((b.transform || {}).filter || []).map((f) => f.column)];
    for (const n of names) { const c = n && t.col(n); if (c && !out.has(n)) out.set(n, c); }
  }
  return out;
}
function renderLabels() {
  const targets = labelTargets(), given = SPEC.value_labels || {}, cols = usedColumns();
  $("#cmp-labels").hidden = !targets.size && !cols.size;
  const nGiven = [...targets.keys()].reduce((k, n) => k + Object.keys(given[n] || {}).length, 0);
  $("#cmp-labels-n").textContent = nGiven && PROPOSAL ? `· ${nGiven} value label${nGiven === 1 ? "" : "s"} proposed by the model; change anything freely` : "· rename variables and coded values such as advice_first";
  $("#cmp-labels-body").innerHTML = `<div class="cmp-lab-col">Variable names <span class="muted">· how a variable is called on axes, in captions, tooltips and table heads</span></div><div class="cmp-lab-grid">`
    + [...cols.entries()].map(([n, c]) => `<label class="cmp-lab"><code title="${esc(n)}">${esc(c.label0 || c.label)}</code>`
      + `<input type="text" maxlength="60" data-cname="${esc(n)}" class="${(SPEC.column_labels || {})[n] ? "from-model" : ""}" value="${esc(c.label)}"/></label>`).join("") + `</div>`
    + [...targets.entries()].map(([n, e]) => `<div class="cmp-lab-col">${esc(e.col.label)}</div><div class="cmp-lab-grid">`
    + [...e.values].sort().map((v) => `<label class="cmp-lab"><code title="${esc(v)}">${esc(v)}</code>`
      + `<input type="text" maxlength="60" data-lcol="${esc(n)}" data-lval="${esc(v)}" class="${(given[n] || {})[v] ? "from-model" : ""}" value="${esc(displayValue(e.table, e.col, v))}"/></label>`).join("") + `</div>`).join("");
}
let labelTimer = null;
function onLabelInput(e) {
  const cn = e.target.closest("[data-cname]");
  if (cn) {                                                  // a variable renamed
    const name = cn.dataset.cname, text = cn.value.trim();
    SPEC.column_labels = SPEC.column_labels || {};
    if (text) SPEC.column_labels[name] = text; else delete SPEC.column_labels[name];
    setColumnLabels(TABLES, SPEC.column_labels);
    clearTimeout(labelTimer); labelTimer = setTimeout(redrawPreviews, 350); return;
  }
  const el = e.target.closest("[data-lcol]"); if (!el) return;
  const col = el.dataset.lcol, val = el.dataset.lval, text = el.value.trim();
  SPEC.value_labels = SPEC.value_labels || {}; SPEC.value_labels[col] = SPEC.value_labels[col] || {};
  if (text && text !== val) SPEC.value_labels[col][val] = text; else delete SPEC.value_labels[col][val];
  setValueLabels(TABLES, SPEC.value_labels);
  clearTimeout(labelTimer); labelTimer = setTimeout(redrawPreviews, 350);      // the editor itself stays as it is: no lost focus
}
function redrawPreviews() { renderGrid(); }

async function onBlockAction(e) {
  const el = e.target.closest("[data-act]"); if (!el) return;
  const card = el.closest(".cmp-card"); const i = +card.dataset.i; const b = SPEC.blocks[i]; const act = el.dataset.act;
  if (e.type === "input") {                                  // text: keep typing, no re-render
    if (act === "message") b.main_message = el.value;
    if (act === "title") { b.title = el.value; const h = $(`#dash-grid .ed-block[data-i="${i}"] .dash-fig-h`); if (h) h.textContent = el.value || "Untitled block"; }
    return;
  }
  if (act === "opt") {                                        // applied when the field is left (change), not per keystroke
    if (e.type !== "change") return;
    const decl = tplOf(b.template).options.find((o) => o.id === el.dataset.opt);
    b.options = { ...(b.options || {}), [decl.id]: decl.type === "boolean" ? el.checked : decl.type === "string" ? el.value : el.value === "" ? null : +el.value };
    b.origin = "user"; await revalidate(); return;
  }
  if (e.type === "click" && !["up", "down", "dup", "remove", "lock", "unchip", "unfilter", "fapply", "fcancel"].includes(act)) return;
  if (e.type === "change" && ["title", "message"].includes(act)) return;
  b.bindings = b.bindings || {}; b.transform = b.transform || {};
  if (act === "fcol") {                                       // open the inline filter form; nothing changes until Apply
    card.querySelectorAll(".cmp-fform").forEach((x) => x.remove());
    if (!el.value) return;
    const t = TABLES.get(b.unit);
    el.closest(".cmp-slot").insertAdjacentHTML("beforeend", filterFormHtml(t.col(el.value), t.rows));
    const form = card.querySelector(".cmp-fform"), opSel = form.querySelector(".ff-op");
    if (opSel) opSel.onchange = () => { const bb = form.querySelector(".ff-b"), aa = form.querySelector(".ff-a"); if (bb) bb.hidden = opSel.value !== "between"; aa.hidden = opSel.value === "not_null"; };
    return;
  }
  if (act === "fcancel") { card.querySelectorAll(".cmp-fform").forEach((x) => x.remove()); card.querySelector('[data-act="fcol"]').value = ""; return; }
  if (!["up", "down", "lock", "dup", "remove"].includes(act)) b.origin = "user";      // a real edit makes the block yours
  const slot = el.dataset.slot;
  if (act === "bind") { if (el.value) b.bindings[slot] = { ...(b.bindings[slot] || {}), column: el.value }; else delete b.bindings[slot]; }
  else if (act === "agg") b.bindings[slot].agg = el.value;
  else if (act === "chip" && el.value) b.bindings[slot] = { columns: [...((b.bindings[slot] || {}).columns || []), el.value] };
  else if (act === "unchip") b.bindings[slot] = { columns: (b.bindings[slot].columns || []).filter((n) => n !== el.dataset.col) };
  else if (act === "template") { b.template = el.value; b.options = {}; }
  else if (act === "unit") { b.unit = el.value; b.bindings = {}; b.transform = {}; }
  else if (act === "answer") b.answers = [...card.querySelectorAll('[data-act="answer"]:checked')].map((x) => x.value);
  else if (act === "unfilter") b.transform.filter = (b.transform.filter || []).filter((_, k) => k !== +el.dataset.i);
  else if (act === "fapply") {
    const f = readFilterForm(el.closest(".cmp-fform"));
    if (!f) { el.closest(".cmp-fform").classList.add("ff-empty"); return; }
    b.transform.filter = [...(b.transform.filter || []), f];
  }
  else if (act === "up" && i > 0) { [SPEC.blocks[i - 1], SPEC.blocks[i]] = [SPEC.blocks[i], SPEC.blocks[i - 1]]; SELECTED = i - 1; }
  else if (act === "down" && i < SPEC.blocks.length - 1) { [SPEC.blocks[i + 1], SPEC.blocks[i]] = [SPEC.blocks[i], SPEC.blocks[i + 1]]; SELECTED = i + 1; }
  else if (act === "dup") { SPEC.blocks.splice(i + 1, 0, { ...JSON.parse(JSON.stringify(b)), id: `${b.id}-copy`, origin: "user" }); SELECTED = i + 1; }
  else if (act === "remove") { SPEC.blocks.splice(i, 1); SELECTED = -1; }
  else if (act === "lock") { if (LOCKED.has(b.id)) LOCKED.delete(b.id); else LOCKED.add(b.id); renderInspector(); return; }
  await revalidate();
}

async function save() {
  if (!SPEC.blocks.length) { $("#cmp-savemsg").textContent = "Add at least one block first."; return; }
  const title = $("#cmp-title").textContent.replace(/\s+/g, " ").trim(); SPEC.title = title;
  $("#cmp-save").disabled = true;
  try {
    const d = DASH ? await api.updateDashboard(DASH.id, { rev: DASH.rev, title: title || DASH.title, spec: SPEC })
      : await api.createDashboard({ dataset_id: DATASET_ID, title, spec: SPEC, proposal: PROPOSAL });
    location.href = `/dashboard?id=${encodeURIComponent(d.id)}`;
  } catch (e) { $("#cmp-savemsg").textContent = `Could not save: ${e.message}`; $("#cmp-save").disabled = false; }
}

// what the Ask card shows: everything while there is no dashboard yet; folded to one "revise" line after
function renderAskState() {
  const has = SPEC.blocks.length > 0;
  $("#ed-revise").hidden = !has;
  $("#cmp-propose").textContent = has ? "Propose again (replaces the blocks)" : "Propose a dashboard";
  const n = QUESTIONS.filter((q) => q.trim()).length;
  $("#ed-ask-sum").textContent = has ? `Questions and model · ${n} question${n === 1 ? "" : "s"}` : "What do you want to find out?";
}
// the model behind a small chip: credits or the last-used key by default, the full panel one click away
function renderModelChip() {
  const m = ($('input[name="cmp-mode"]:checked') || {}).value, bal = CFG && CFG.credits ? CFG.credits.balance || 0 : 0;
  const text = m === "credits" ? `1 credit · ${bal} left` : m === "local" ? ($("#cmp-lmdl").value.trim() || "local model: not set")
    : ($("#cmp-key").value.trim() ? `${($("#cmp-mdl").selectedOptions[0] || {}).textContent || "own key"}` : "no model set: add a key");
  $("#ed-modelchip").innerHTML = `Model: <b>${esc(text)}</b> ▾`;
  $("#ed-modelchip").classList.toggle("warn", /not set|no model/.test(text));
}

export async function mountEditor() {
  const head = $("#dash-head");
  try {
    const id = params.get("id") || params.get("dashboard");
    if (id) { DASH = await api.dashboard(id, "draft").catch(() => null); if (!DASH || !DASH.can_edit) { location.replace(`/dashboard?id=${encodeURIComponent(id)}`); return; } DATASET_ID = DASH.dataset_id; }
    if (!DATASET_ID) { head.innerHTML = '<p class="muted">No dataset given.</p>'; return; }
    [REG, CFG, MODELS] = await Promise.all([api.analysisTemplates(), api.extractionConfig().catch(() => null), api.models().then((m) => m.providers || {}).catch(() => ({}))]);
    const t = await loadTable(DATASET_ID, null);            // the preset's default row unit
    TABLES.set(t.unit.id, t); EVIDENCE.set(t.unit.id, createEvidence(t, $("#dash-panel")));
    const d = t.dataset, back = DASH ? `/dashboard?id=${encodeURIComponent(DASH.id)}` : `/dataset?id=${encodeURIComponent(d.id)}`;
    document.body.classList.add("dash-editing");
    head.innerHTML = `<div><a class="muted" href="/dataset?id=${encodeURIComponent(d.id)}">← ${esc(d.title || "Dataset")}</a>`
      + `<h2 id="cmp-title" class="ed-title" contenteditable="plaintext-only" spellcheck="false" data-ph="Dashboard title" style="margin:.2em 0 4px"></h2>`
      + `<div class="muted ds-sub">${DASH ? "Editing your draft" : "New dashboard"} · live data · <span id="cmp-title-src"></span></div></div>`
      + `<div class="dash-toolbar"><button type="button" class="btn btn-ghost btn-sm" id="ed-labels-btn" title="rename variables and coded values">Labels</button>`
      + `<button type="button" class="btn btn-ghost btn-sm" id="ed-style-btn" title="look, font and colours">🎨 Style</button>`
      + `<a class="btn btn-ghost btn-sm" href="${back}">Cancel</a><button type="button" class="btn btn-primary btn-sm" id="cmp-save">Save</button></div>`;
    $("#dash-controls").hidden = true; $("#dash-foot").hidden = true;
    $("#dash-owner").innerHTML = `<details class="ds-card ed-ask" id="ed-ask" open><summary class="ds-card-h" id="ed-ask-sum">What do you want to find out?</summary>`
      + `<p class="muted cmp-lede">Write down the questions you would like to answer. A model turns them into a dashboard out of predefined building blocks (figures, tables, key numbers); it appears below, drawn from your data, and you change whatever you like.</p>`
      + `<div id="cmp-questions"></div>`
      + `<label class="cmp-context"><span>Anything else the planner should know <span class="muted">(optional)</span></span>`
      + `<textarea id="cmp-context" rows="2" maxlength="2000" placeholder="Background, audience and requests, e.g. “for clinicians; only accuracy outcomes; keep it to four figures”"></textarea></label>`
      + `<div class="ed-askrow"><button class="btn btn-primary" id="cmp-propose">Propose a dashboard</button>`
      + `<button class="btn btn-ghost" id="cmp-default" title="built from the column types, no model involved">Start from the default dashboard</button>`
      + `<button type="button" class="ed-chip" id="ed-modelchip" aria-expanded="false"></button></div>`
      + `<div class="ed-pop" id="ed-modelpop" hidden><div id="cmp-model"></div></div></details>`
      + `<div class="cmp-revise" id="ed-revise" hidden><input id="cmp-feedback" type="text" placeholder="Ask the model to revise, e.g. “compare by task type instead”; blocks marked 🔒 are kept"/>`
      + `<button class="btn btn-ghost" id="cmp-repropose">Revise with the model</button></div>`
      + `<p class="muted" id="cmp-status"></p><div id="cmp-failure"></div>`;
    $("#dash-style").innerHTML = `<div id="ed-stylehost"></div><details class="ds-card cmp-labels" id="cmp-labels" hidden><summary class="ds-card-h">Labels <span class="muted" id="cmp-labels-n"></span></summary>`
      + `<p class="muted cmp-lede">Rename variables and coded values: how they are written on axes, in legends, captions, tooltips and tables. Only the wording changes; the data and the filters keep the original values.</p><div id="cmp-labels-body"></div></details>`;
    $("#dash-unanswered").innerHTML = `<div id="cmp-report"></div><p class="muted" id="dash-empty">No blocks yet: propose a dashboard above, start from the default one, or add blocks yourself.</p>`;
    $("#dash-foot").insertAdjacentHTML("beforebegin", `<p class="muted" id="cmp-savemsg" style="font-size:13px"></p>`);

    if (DASH) { SPEC = DASH.spec; QUESTIONS = (SPEC.questions || []).map((q) => q.text); if (!QUESTIONS.length) QUESTIONS = [""]; }
    applyTheme(SPEC.theme);
    renderQuestions(); renderModelPanel(); renderModelChip();
    $("#cmp-model").addEventListener("input", renderModelChip); $("#cmp-model").addEventListener("change", renderModelChip);
    $("#ed-modelchip").onclick = () => { const p = $("#ed-modelpop"); p.hidden = !p.hidden; $("#ed-modelchip").setAttribute("aria-expanded", String(!p.hidden)); };
    $("#cmp-propose").onclick = () => propose("");
    $("#cmp-default").onclick = useDefault;
    $("#cmp-repropose").onclick = () => propose($("#cmp-feedback").value.trim() || "Improve the proposal.");
    $("#cmp-save").onclick = save;
    $("#cmp-labels-body").addEventListener("input", onLabelInput);
    $("#ed-labels-btn").onclick = () => { const l = $("#cmp-labels"); l.hidden = false; l.open = !l.open; if (l.open) l.scrollIntoView({ behavior: "smooth", block: "nearest" }); };
    $("#ed-style-btn").onclick = () => {
      const host = $("#ed-stylehost"); if (host.innerHTML) { host.innerHTML = ""; return; }
      mountStylePanel(host, { registry: REG, theme: SPEC.theme, onSave: async (theme) => { SPEC.theme = theme; applyTheme(theme); } });   // kept with Save
    };
    $("#cmp-context").value = SPEC.context || "";
    $("#cmp-context").addEventListener("input", (e) => { SPEC.context = e.target.value.trim(); });
    const title = $("#cmp-title");
    title.addEventListener("input", () => { TITLE_TYPED = true; SPEC.title = title.textContent.trim(); $("#cmp-title-src").textContent = ""; });
    title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } });
    const insp = $("#dash-inspector");
    insp.addEventListener("change", onBlockAction); insp.addEventListener("input", onBlockAction); insp.addEventListener("click", onBlockAction);
    if (DASH) { $("#ed-ask").open = false; await revalidate(); if (DASH.proposal && DASH.proposal.raw) showModelOutput({ proposal: DASH.proposal }, { stored: true }); }
    else { renderGrid(); renderAskState(); $("#cmp-title").textContent = ""; $("#cmp-title-src").textContent = "the model proposes a title; click it to write your own"; }
  } catch (e) { head.innerHTML = `<p class="muted">Couldn’t open the editor: ${esc(e.message)}</p>`; }
}
