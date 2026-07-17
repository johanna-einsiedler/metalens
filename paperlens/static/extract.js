// Extraction workflow — sidebar nav + progressive-disclosure accordion.
// Steps fold by default and any can be opened by clicking (header or sidebar);
// completing a step opens the next. Step 2 mirrors the old version: provider →
// model (from models.json) → API key + test connection.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { saveToWorkspace } from "/static/save.js";
import { getKey, setKey } from "/static/keys.js";

const $ = (s) => document.querySelector(s);
let task = null;          // extract | label | summarise | workflow
let presetId = null;
let presets = [];
let MODELS = {};
let ADD_DATASET = null;   // {id,title,schema_id,prompt,model} when ?dataset= (add-papers mode)
let USE_CREDITS = false;  // logged-in keyless run on Metalens's server key + fixed model

const PROVIDER_LABEL = { openai: "OpenAI", google: "Google Gemini", anthropic: "Anthropic",
                         deepseek: "DeepSeek", mistral: "Mistral" };

const steps = [...document.querySelectorAll(".acc-step")];
const stepEl = (n) => document.querySelector(`.acc-step[data-step="${n}"]`);
function setNav(key) {
  document.querySelectorAll(".wf-step-nav").forEach((w) => w.classList.toggle("active", w.dataset.step === String(key)));
}
function openStep(n) { steps.forEach((s) => s.classList.toggle("open", +s.dataset.step === n)); setNav(n); }
function done(n) { stepEl(n).classList.add("done"); }
function summary(n, t) { stepEl(n).querySelector(".acc-sum").textContent = t; }

async function init() {
  try { presets = (await api.presets()).presets || []; } catch { /* */ }
  await loadModels();
  document.querySelectorAll(".task-card[data-task]").forEach((c) =>
    (c.onclick = () => selectTask(c.dataset.task, c)));
  // any step / sidebar item can be unfolded by clicking
  steps.forEach((s) => (s.querySelector(".acc-head").onclick = () => openStep(+s.dataset.step)));
  document.querySelectorAll(".wf-step-nav").forEach((w) => (w.onclick = () => {
    if (w.dataset.step === "results") { setNav("results"); $("#result").scrollIntoView({ behavior: "smooth" }); return; }
    openStep(+w.dataset.step);
  }));
  document.querySelectorAll("[data-back]").forEach((b) => (b.onclick = () => openStep(+b.dataset.back)));
  $("#next2").onclick = afterModel;
  $("#next4").onclick = () => { done(4); summary(4, "reviewed"); openStep(5); };
  $("#testconn").onclick = testConnection;
  $("#apikey").oninput = () => setKey($("#provider").value, $("#apikey").value.trim());
  $("#run").onclick = run;
  // step 3: structured designer (substeps) + freeform toggle
  document.querySelectorAll(".sf-pill").forEach((p) => (p.onclick = () => openSub(+p.dataset.sub)));
  $("#sfNext").onclick = () => (sub < 4 ? openSub(sub + 1) : genStruct());
  $("#sfBack").onclick = () => (sub === 1 ? openStep(2) : openSub(sub - 1));
  $("#addfield").onclick = () => addField({});
  $("#addtab").onclick = () => addTab();
  $("#tabsadd").onclick = () => addTab();
  $("#sf-unit").oninput = updateUnitEcho;
  $("#usepaste").onclick = usePaste;
  document.querySelectorAll(".mode-btn").forEach((b) => (b.onclick = () => showMode(b.dataset.mode)));
  $("#simpleGen").onclick = genSimple;
  $("#masemUse").onclick = masemUse;
  ["masemEffectSizes", "masemVariables", "masemScaleName", "masemNItems", "masemItems"].forEach((id) => {
    const el = $("#" + id); if (el) { el.addEventListener("input", refreshMasemPreview); el.addEventListener("change", refreshMasemPreview); }
  });
  renderUnitPresets();
  renderSimpleUnits();
  syncTabsUI();
  setupTooltips();
  setupDropzone();
  await setupCredits();          // resolve credits first so add-papers can reflect them
  await maybeAddPapersMode();
}

// Offer keyless extraction on Metalens credits when the user is logged in, the
// server is configured to provide it, and they have a positive balance. Selecting
// "credits" hides the own-key block; the server picks the model + uses its own key.
async function setupCredits() {
  const sw = $("#credit-switch"); if (!sw) return;
  let c = null;
  try { c = await api.credits(); } catch { return; }   // 401 for anon → no toggle
  if (!c || !c.offered || (c.balance || 0) <= 0) return;
  USE_CREDITS = true;                                    // default to credits when available
  $("#credit-left").textContent = `(${c.balance} left)`;
  if (c.model) $("#credit-model").textContent = `· ${c.model}`;
  sw.hidden = false;
  applyKeyMode();
  sw.querySelectorAll('input[name="keymode"]').forEach((r) => (r.onchange = () => {
    USE_CREDITS = sw.querySelector('input[name="keymode"]:checked').value === "credits";
    applyKeyMode();
  }));
}
function applyKeyMode() {
  const block = $("#ownkey-block"); if (block) block.hidden = USE_CREDITS;
}

// ── add-papers mode: /extract?dataset=<id> reuses a dataset's saved recipe ────
// Prefill provider/model + prompt + schema, jump to the upload step, and on save
// assign the new papers to that dataset (no new dataset is created).
async function maybeAddPapersMode() {
  const dsId = new URLSearchParams(location.search).get("dataset");
  if (!dsId) return;
  let ds;
  try { ds = await api.dataset(dsId); } catch { return; }
  // schema_id: prefer the dataset's recorded recipe; fall back to its records' schema
  // (older datasets were saved without a recipe — infer it so add-papers still works).
  let schemaId = ds.schema_id || null;
  if (!schemaId) {
    try { const r = await api.datasetRows([dsId]); schemaId = (r.rows && r.rows[0] && r.rows[0].schema_id) || null; }
    catch { /* leave null → generic flow below */ }
  }
  ADD_DATASET = { id: dsId, title: ds.title || "dataset", schema_id: schemaId,
                  prompt: ds.prompt || "", model: ds.model || "" };
  // derive task/presetId from the schema so the rest of the UI stays consistent
  const base = (schemaId || "extract@v1").replace(/@.*$/, "");
  if (base === "summarize") { task = "summarise"; }
  else if (base === "extract" || base === "label") { task = base; }
  else { task = "workflow"; presetId = base; }
  // prompt: dataset's recorded prompt, else fetch it from the (file or personal) preset
  if (!ADD_DATASET.prompt && base && task === "workflow") {
    try { ADD_DATASET.prompt = (await api.presetPrompt(base)).prompt || ""; } catch { /* */ }
  }
  if (ds.model) selectProviderModel(ds.model);
  if (ADD_DATASET.prompt) $("#prompt").value = ADD_DATASET.prompt;
  // fast-forward the accordion to Upload & extract
  done(1); summary(1, `Adding to “${ADD_DATASET.title}”`);
  done(2); summary(2, USE_CREDITS ? "Metalens credits" : ($("#model").value ? `${$("#model").value} · key ••••` : "set your model & key"));
  done(3); summary(3, "reusing saved prompt");
  done(4); summary(4, "reviewed");
  openStep(5);
  const body = stepEl(5).querySelector(".acc-body");
  if (body && !document.querySelector("#addbanner")) {
    const b = document.createElement("div");
    b.id = "addbanner"; b.className = "add-banner";
    b.innerHTML = `Adding papers to <b>${esc(ADD_DATASET.title)}</b> — they’ll reuse this dataset’s model, prompt & schema. `
      + `<a href="/dataset?id=${esc(dsId)}">Back to dataset →</a>`;
    body.prepend(b);
  }
}

// Select the provider whose model list contains `value`, then that model. If the
// saved model isn't in models.json, add it as an option so it's still used.
function selectProviderModel(value) {
  for (const p of Object.keys(MODELS)) {
    if ((MODELS[p] || []).some((m) => m.value === value)) {
      $("#provider").value = p; fillModels(); break;
    }
  }
  const sel = $("#model");
  if (sel.value !== value) {
    const opt = document.createElement("option");
    opt.value = value; opt.textContent = value; sel.appendChild(opt);
  }
  sel.value = value;
}

// hover-for-example tooltip, positioned in the gutter beside the content column
function setupTooltips() {
  const tip = document.createElement("div"); tip.className = "tip-pop"; document.body.appendChild(tip);
  document.body.addEventListener("mouseover", (e) => {
    const el = e.target.closest(".hl"); if (!el || !el.dataset.tip) return;
    tip.textContent = el.dataset.tip; tip.style.display = "block";
    const col = (document.querySelector("main") || document.querySelector(".wrap")).getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const gap = 10;
    // hug the content column, on the side matching the field's column; align to the hovered row
    const goLeft = (r.left + r.width / 2) <= (col.left + col.width / 2);
    let left = goLeft ? col.left - tip.offsetWidth - gap : col.right + gap;
    if (!goLeft && left + tip.offsetWidth > window.innerWidth - 8) left = col.left - tip.offsetWidth - gap;  // flip if no room
    if (goLeft && left < 8) left = col.right + gap;
    tip.style.left = Math.max(8, left) + "px";
    tip.style.top = Math.min(window.innerHeight - tip.offsetHeight - 8, Math.max(8, r.top)) + "px";
  });
  document.body.addEventListener("mouseout", (e) => { if (e.target.closest(".hl")) tip.style.display = "none"; });
}

// ── models (provider → model cascade) ──────────────────────────────────────
async function loadModels() {
  try { MODELS = (await api.models()).providers || {}; } catch { MODELS = {}; }
  const provs = Object.keys(MODELS);
  $("#provider").innerHTML = provs.map((p) => `<option value="${p}">${esc(PROVIDER_LABEL[p] || p)}</option>`).join("");
  $("#provider").onchange = fillModels;
  if (provs.length) fillModels();
}
function fillModels() {
  const p = $("#provider").value;
  const ms = MODELS[p] || [];
  $("#model").innerHTML = ms.map((m) => `<option value="${esc(m.value)}">${esc(m.label)}</option>`).join("");
  $("#keylabel").textContent = `${PROVIDER_LABEL[p] || p} API key`;
  $("#apikey").value = getKey(p);   // auto-fill this browser's saved key (never server-stored)
}

async function testConnection() {
  const key = $("#apikey").value;
  if (!key) { $("#teststatus").textContent = "enter your API key first"; return; }
  $("#testconn").disabled = true; $("#teststatus").textContent = "testing…";
  try {
    const r = await api.testKey({ model: $("#model").value, api_key: key });
    $("#teststatus").textContent = r.ok ? "✓ Connection OK" : "✗ " + (r.error || "failed");
  } catch (e) { $("#teststatus").textContent = "✗ " + e.message; }
  finally { $("#testconn").disabled = false; }
}

// ── step 1: task ────────────────────────────────────────────────────────────
function selectTask(t, card) {
  task = t; presetId = null;
  document.querySelectorAll(".task-card[data-task]").forEach((x) => x.classList.remove("sel"));
  card.classList.add("sel");
  if (t === "workflow") {
    const items = presets.filter((p) => p.mode === "extraction");
    $("#taskdetail").innerHTML =
      `<div class="muted" style="font-size:13px;margin:6px 0 8px">Pick a pre-built method — a complete, tested prompt that skips the prompt-design step:</div>`
      + `<div class="method-grid">` + (items.map((p) =>
          `<button type="button" class="method-card" data-pid="${esc(p.preset_id)}">`
          + `<span class="mc-title">${esc(p.title)}`
          + (p.personal ? ` <span class="mc-badge">${p.owned ? "Personal" : "Shared"}</span>` : "")
          + `</span>`
          + `<span class="mc-sub">${esc(p.tagline || "")}</span></button>`).join("")
          || '<span class="muted">No pre-built methods available.</span>')
      + `</div>`;
    $("#taskdetail").querySelectorAll(".method-card").forEach((b) => (b.onclick = () => {
      presetId = b.dataset.pid;
      document.querySelectorAll(".method-card").forEach((x) => x.classList.toggle("sel", x === b));
      const p = presets.find((x) => x.preset_id === presetId);
      advance(`Workflow: ${p ? p.title : presetId}`);
    }));
  } else {
    $("#taskdetail").innerHTML = "";
    advance({ extract: "Extract data", label: "Label a paper", summarise: "Summarise a paper" }[t]);
  }
}
function advance(sum) { done(1); summary(1, sum); openStep(2); }

// ── step 2: model → 3 (describe) or 4 (preset prompt) ──────────────────────
async function afterModel() {
  if (!USE_CREDITS && !$("#apikey").value) { $("#teststatus").textContent = "enter your API key"; return; }
  summary(2, USE_CREDITS ? "Metalens credits" : `${$("#model").value} · key ••••`); done(2);
  if (task === "extract" || task === "label") {
    showMode("simple");
    openStep(3);
  } else if (isMasemPreset(presetId)) {
    await openMasemBuilder(presetId);   // guided MASEMiner builder (Direct/Indirect)
    openStep(3);
  } else {
    summary(3, "auto (pre-built prompt)"); done(3);
    const pid = task === "summarise" ? "summarize" : presetId;
    if (pid) { try { $("#prompt").value = (await api.presetPrompt(pid)).prompt; } catch { $("#prompt").value = ""; } }
    openStep(4);
  }
}

// ── step 3: structured "describe what to extract" designer (substeps) ───────
let sub = 1;
let unitChosen = false;   // substep 1: unit block + Next stay hidden until a template is picked
let lastFieldDefs = null;
// step 3 has three levels: guided (default) · advanced (structured designer) · own prompt
let MODE = "simple";
function showMode(m) {
  MODE = m;
  const ms = document.querySelector(".mode-switch"); if (ms) ms.hidden = false;
  const mb = $("#masemBuilder"); if (mb) mb.hidden = true;
  $("#simpleform").hidden = m !== "simple";
  $("#structform").hidden = m !== "advanced";
  $("#pasteflow").hidden = m !== "own";
  document.querySelectorAll(".mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
  if (m === "advanced") openSub(sub || 1);
}
const isMasemPreset = (pid) => typeof pid === "string" && pid.startsWith("masem");

// ── MASEMiner guided builder (one preset → Direct/Indirect toggle + live preview) ─
const MASEM = { starter: null, defaults: {}, cache: {}, timer: null };
const MASEM_STARTERS = [
  { id: "masem", label: "Direct information", tag: "Extract correlations from text and table(s)." },
  { id: "masem-ncs18", label: "Indirect information", tag: "Extract factor loadings and factor correlations from text and table(s)." },
];

async function openMasemBuilder(pid) {
  const ms = document.querySelector(".mode-switch"); if (ms) ms.hidden = true;
  $("#simpleform").hidden = true; $("#structform").hidden = true; $("#pasteflow").hidden = true;
  $("#masemBuilder").hidden = false;
  renderMasemStarters();
  await selectMasemStarter(isMasemPreset(pid) ? pid : "masem", false);
}
function renderMasemStarters() {
  const box = $("#masem-starters"); if (!box) return;
  box.innerHTML = MASEM_STARTERS.map((s) =>
    `<button type="button" class="masem-starter${s.id === MASEM.starter ? " sel" : ""}" data-sid="${esc(s.id)}">`
    + `<div class="ms-label">${esc(s.label)}</div><div class="ms-tag">${esc(s.tag)}</div></button>`).join("");
  box.querySelectorAll(".masem-starter").forEach((b) => (b.onclick = () => selectMasemStarter(b.dataset.sid, true)));
}
async function selectMasemStarter(pid, isUserClick) {
  if (isUserClick && MASEM.starter === pid) return;
  let detail = MASEM.cache[pid];
  if (!detail) { try { detail = await api.presetDetail(pid); MASEM.cache[pid] = detail; } catch { return; } }
  MASEM.starter = pid;
  presetId = pid;                       // schemaIdFor() → `${pid}@v1` (masem / masem-ncs18)
  MASEM.defaults = JSON.parse(JSON.stringify(detail.template_params || {}));
  renderMasemStarters();
  const direct = pid === "masem";
  $("#masemFormDirect").hidden = !direct;
  $("#masemFormIndirect").hidden = direct;
  populateMasemForm(MASEM.defaults);
  if (MASEM.timer) { clearTimeout(MASEM.timer); MASEM.timer = null; }
  await doMasemPreview();
}
function populateMasemForm(d) {
  const es = $("#masemEffectSizes"); if (es) { es.value = ""; es.placeholder = serialiseEffectSizes(d.effect_sizes) || "r: Correlation\nor: Odds ratios"; }
  const vs = $("#masemVariables"); if (vs) { vs.value = ""; vs.placeholder = serialiseVariables(d.variables) || "bm: A measure of body mass such as BMI or waist circumference\nvg: A measure of video-game use — hours/day or session frequency\npa: A measure of physical activity — exercise length or frequency"; }
  const sn = $("#masemScaleName"); if (sn) { sn.value = ""; const nm = d.scale_name || d.instrument_name; sn.placeholder = (nm && nm !== "the target scale" && nm !== "the target instrument") ? `e.g. ${nm}` : "e.g. Need for Cognition Scale (NCS-18)"; }
  const ni = $("#masemNItems"); if (ni) ni.value = "";
  const it = $("#masemItems"); if (it) {
    it.value = ""; const items = Array.isArray(d.item_texts) ? d.item_texts : [];
    it.placeholder = items.length ? items.slice(0, 3).map((t, i) => `${i + 1}: ${t}`).join("\n") + (items.length > 3 ? `\n…  (${items.length - 3} more example items used by default)` : "") : "1: <first item text>\n2: <second item text>\n3: <third item text>\n…";
  }
}
function readMasemParams() {
  const p = {};
  if (MASEM.starter === "masem") {
    const es = parseEffectSizes($("#masemEffectSizes").value); if (es.length) p.effect_sizes = es;
    const vs = parseVariables($("#masemVariables").value); if (vs.length) p.variables = vs;
  } else {
    const sn = ($("#masemScaleName").value || "").trim();
    if (sn) { p.scale_name = sn; p.instrument_name = sn; p.instrument_name_long = sn; }
    const n = parseInt($("#masemNItems").value, 10); if (Number.isFinite(n) && n > 0) p.n_items = n;
    const items = parseItems($("#masemItems").value); if (items.length) { p.item_texts = items; p.include_item_texts = true; }
  }
  return p;   // empty fields fall back to preset defaults (merged server-side)
}
async function doMasemPreview() {
  const pid = MASEM.starter; if (!pid) return;
  try {
    const r = await api.buildPresetPrompt({ preset_id: pid, template_params: readMasemParams() });
    $("#masemPreviewBox").textContent = r.prompt || "";
    $("#masemPreviewLen").textContent = (r.prompt || "").length;
    $("#prompt").value = r.prompt || "";   // the exact extraction prompt
  } catch { /* best-effort preview */ }
}
function refreshMasemPreview() { if (MASEM.timer) clearTimeout(MASEM.timer); MASEM.timer = setTimeout(doMasemPreview, 350); }
async function masemUse() {
  await doMasemPreview();
  if (!$("#prompt").value.trim()) { alert("Could not build a prompt — fill in the fields."); return; }
  summary(3, `MASEMiner · ${MASEM.starter === "masem" ? "direct" : "indirect"}`); done(3); openStep(4);
}
function parseEffectSizes(text) {
  return (text || "").split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
    const m = line.match(/^([\w.+-]+)\s*[:=—-]\s*(.+)$/);
    return m ? { code: m[1].trim(), label: m[2].trim() } : { code: line, label: "" };
  });
}
function serialiseEffectSizes(list) {
  if (!Array.isArray(list) || !list.length) return "";
  return list.map((e) => (typeof e === "string" ? e : (e && e.code ? (e.label ? `${e.code}: ${e.label}` : e.code) : ""))).filter(Boolean).join("\n");
}
function parseVariables(text) {
  return (text || "").split("\n").map((l) => l.trim()).filter(Boolean).map((line) => {
    const syn = line.split(/\s*::\s*/); const head = syn[0];
    const synonyms = syn[1] ? syn[1].split(/\s*,\s*/).map((s) => s.trim()).filter(Boolean) : [];
    const m = head.match(/^([\w.+-]+)\s*[:=—-]\s*(.+)$/);
    return m ? { name: m[1].trim(), definition: m[2].trim(), synonyms } : { name: head, definition: "", synonyms };
  });
}
function serialiseVariables(list) {
  if (!Array.isArray(list) || !list.length) return "";
  return list.map((v) => { if (!v || !v.name) return ""; const head = v.definition ? `${v.name}: ${v.definition}` : v.name; return (Array.isArray(v.synonyms) && v.synonyms.length) ? `${head} :: ${v.synonyms.join(", ")}` : head; }).filter(Boolean).join("\n");
}
function parseItems(text) {
  return (text || "").split("\n").map((l) => l.trim()).filter(Boolean).map((l) => l.replace(/^\s*\d+\s*[:.)]\s*/, "").trim()).filter(Boolean);
}
function cap(s) { s = String(s); return s.charAt(0).toUpperCase() + s.slice(1); }
function slug(s) { return String(s).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "field"; }
function parseFields(text) {  // still used for the paper-level #sf-meta textarea
  return (text || "").split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const i = l.indexOf(":");
    return i < 0 ? { name: slug(l), desc: l } : { name: l.slice(0, i).trim(), desc: l.slice(i + 1).trim() };
  });
}

function openSub(n) {
  sub = n;
  document.querySelectorAll("#structform .sf-sub").forEach((s) => (s.hidden = +s.dataset.sub !== n));
  document.querySelectorAll(".sf-pill").forEach((p) => p.classList.toggle("active", +p.dataset.sub === n));
  $("#sfNext").textContent = n === 4 ? "Generate prompt & preset →" : "Next ▸";
  // substep 1 reveals the unit block + Next only after a template is chosen
  if (n === 1) { $("#unit-block").hidden = !unitChosen; $("#sfNext").hidden = !unitChosen; }
  else { $("#sfNext").hidden = false; }
  if (n === 2) { updateUnitEcho(); syncTabsUI(); }
}
function updateUnitEcho() { const e = $("#sf-unit-echo"); if (e) e.textContent = $("#sf-unit").value.trim() || "record"; }

// review tabs (each becomes a sub_view; field.tab references a tab id) ──────
let TABS = [{ id: "details", label: "Details" }];
let tabSeq = 0;
function fillTabSelect(selectEl, current) {
  selectEl.innerHTML = TABS.map((t) => `<option value="${t.id}">${esc(t.label)}</option>`).join("");
  selectEl.value = TABS.some((t) => t.id === current) ? current : "details";
}
function refreshTabSelects() {
  const multi = TABS.length > 1;
  document.querySelectorAll("#sf-fields .field-row").forEach((row) => {
    const selectEl = row.querySelector(".fr-tab");
    fillTabSelect(selectEl, selectEl.value || "details");
    row.querySelector(".fr-tab-wrap").hidden = !multi;
  });
}
function renderTabsEditor() {
  const box = $("#sf-tabs"); if (!box) return;
  box.innerHTML = TABS.map((t, i) =>
    `<div class="tab-row" data-i="${i}"><input class="tab-label" value="${esc(t.label)}"/>`
    + (TABS.length > 1 ? `<button class="tab-rm fr-rm" type="button" title="remove tab">✕</button>` : "")
    + `</div>`).join("");
  box.querySelectorAll(".tab-row").forEach((rowEl) => {
    const i = +rowEl.dataset.i;
    rowEl.querySelector(".tab-label").oninput = (e) => { TABS[i].label = e.target.value; refreshTabSelects(); };
    const rm = rowEl.querySelector(".tab-rm");
    if (rm) rm.onclick = () => {
      const removed = TABS[i].id; TABS.splice(i, 1);
      if (!TABS.length) TABS = [{ id: "details", label: "Details" }];
      document.querySelectorAll("#sf-fields .fr-tab").forEach((s) => { if (s.value === removed) s.value = "details"; });
      syncTabsUI(); refreshTabSelects();
    };
  });
}
function addTab(label) {
  TABS.push({ id: `tab${++tabSeq}`, label: label || `Tab ${TABS.length + 1}` });
  syncTabsUI(); refreshTabSelects();
}
// Review tabs are pointless with a single category: show the editor only when
// there are ≥2 tabs; otherwise offer a one-click "group into tabs" affordance.
function syncTabsUI() {
  const multi = TABS.length > 1;
  $("#tabsblock").hidden = !multi;
  $("#tabsadd").hidden = multi;
  if (multi) renderTabsEditor();
}

// field rows (Value / List / Table) ─────────────────────────────────────────
function addField(pre = {}) {
  const row = document.createElement("div");
  row.className = "field-row";
  row.innerHTML = `
    <div class="fr-main">
      <input class="fr-name" placeholder="field_name"/>
      <input class="fr-desc" placeholder="what it is — e.g. the reported coefficient (number)"/>
      <select class="fr-type">
        <option value="value">Value</option><option value="list">List</option><option value="table">Table</option>
      </select>
      <span class="fr-tab-wrap" hidden><span class="hl" data-tip="Which review tab this field shows under in the workspace. Tabs are review-only groupings (each gets its own confidence rating) — they don't change the JSON; your fields stay flat on the record.">tab</span>:&nbsp;<select class="fr-tab"></select></span>
      <button class="fr-rm" type="button" title="remove field">✕</button>
    </div>
    <div class="fr-cols" hidden>
      <div class="fc-hd sf-help">Columns — one object per row of this table:</div>
      <div class="fc-list"></div>
      <button class="btn btn-ghost fc-add" type="button">+ column</button>
    </div>`;
  row.querySelector(".fr-rm").onclick = () => row.remove();
  const typeSel = row.querySelector(".fr-type");
  const cols = row.querySelector(".fr-cols");
  typeSel.onchange = () => { const t = typeSel.value === "table"; cols.hidden = !t; if (t && !cols.querySelector(".fc-row")) addColumn(row); };
  row.querySelector(".fc-add").onclick = () => addColumn(row);
  row.querySelector(".fr-name").value = pre.name || "";
  row.querySelector(".fr-desc").value = pre.desc || "";
  typeSel.value = pre.type || "value";
  $("#sf-fields").appendChild(row);
  if (pre.type === "table") { cols.hidden = false; (pre.columns || []).forEach((c) => addColumn(row, c)); if (!cols.querySelector(".fc-row")) addColumn(row); }
  fillTabSelect(row.querySelector(".fr-tab"), pre.tab || "details");
  row.querySelector(".fr-tab-wrap").hidden = TABS.length <= 1;
  return row;
}
function addColumn(row, c = {}) {
  const r = document.createElement("div");
  r.className = "fc-row";
  r.innerHTML = `<input class="fc-name" placeholder="column_name"/><input class="fc-desc" placeholder="what it holds (e.g. number, null)"/><button class="fc-rm" type="button" title="remove column">✕</button>`;
  r.querySelector(".fc-rm").onclick = () => r.remove();
  r.querySelector(".fc-name").value = c.name || "";
  r.querySelector(".fc-desc").value = c.desc || "";
  row.querySelector(".fc-list").appendChild(r);
}
function collectFields() {
  return [...document.querySelectorAll("#sf-fields .field-row")].map((row) => {
    const raw = row.querySelector(".fr-name").value.trim();
    if (!raw) return null;
    const type = row.querySelector(".fr-type").value;
    const f = { name: slug(raw), desc: row.querySelector(".fr-desc").value.trim(),
                type, tab: row.querySelector(".fr-tab").value || "details" };
    if (type === "table") f.columns = [...row.querySelectorAll(".fr-cols .fc-row")].map((c) => {
      const cn = c.querySelector(".fc-name").value.trim();
      return cn ? { name: slug(cn), desc: c.querySelector(".fc-desc").value.trim() } : null;
    }).filter(Boolean);
    return f;
  }).filter(Boolean);
}

// unit-of-analysis starter templates ────────────────────────────────────────
const UNIT_PRESETS = {
  regression: { title: "Regression result", unit: "regression", cardinality: "many", id: "regression_id", label: "{dependent_var} ({model_type})",
    fields: [
      { name: "regression_id", desc: "stable id, e.g. 'T3-col2'", type: "value", tab: "details" },
      { name: "dependent_var", desc: "the outcome / left-hand-side variable", type: "value", tab: "details" },
      { name: "model_type", desc: "OLS | IV | logit | diff-in-diff | …", type: "value", tab: "details" },
      { name: "estimates", desc: "one row per displayed regressor", type: "table", tab: "estimates",
        columns: [ { name: "regressor", desc: "the right-hand-side variable name" }, { name: "coefficient", desc: "point estimate (number)" },
                   { name: "std_error", desc: "standard error (number or null)" }, { name: "p_value", desc: "p-value (number or null)" } ] },
      { name: "fixed_effects", desc: "list of FE dimensions, e.g. firm, year", type: "list", tab: "spec" },
      { name: "standard_errors", desc: "SE type: robust | clustered(level) | …", type: "value", tab: "spec" },
      { name: "is_headline", desc: "true if this is a headline / preferred result", type: "value", tab: "details" } ] },
  finding: { title: "Finding / effect size", unit: "finding", cardinality: "many", id: "", label: "{subtopic}: {metric}",
    fields: [
      { name: "metric", desc: "the named outcome being measured", type: "value", tab: "details" },
      { name: "value", desc: "the reported coefficient / effect (number)", type: "value", tab: "details" },
      { name: "ci_low", desc: "lower 95% CI bound (number or null)", type: "value", tab: "details" },
      { name: "ci_high", desc: "upper 95% CI bound (number or null)", type: "value", tab: "details" },
      { name: "direction", desc: "positive | negative | null", type: "value", tab: "details" },
      { name: "p_value", desc: "p-value (number or null)", type: "value", tab: "details" },
      { name: "comparison", desc: "what was compared to what", type: "value", tab: "details" },
      { name: "subtopic", desc: "topical bucket, e.g. productivity | inequality", type: "value", tab: "details" } ] },
  study: { title: "Study / sample", unit: "sample", cardinality: "many", id: "sample_id", label: "Sample {sample_id}",
    fields: [
      { name: "sample_id", desc: "stable id for this sample", type: "value", tab: "descriptives" },
      { name: "n", desc: "sample size (integer or null)", type: "value", tab: "descriptives" },
      { name: "country", desc: "country / region of the sample", type: "value", tab: "descriptives" },
      { name: "year", desc: "data collection / publication year", type: "value", tab: "descriptives" },
      { name: "factor_loadings", desc: "item × factor standardised loadings", type: "table", tab: "loadings",
        columns: [ { name: "item", desc: "item number or short text" }, { name: "factor", desc: "factor label, e.g. F1" }, { name: "loading", desc: "standardised loading (number or null)" } ] },
      { name: "correlations", desc: "variable × variable correlation matrix", type: "table", tab: "correlations",
        columns: [ { name: "variable", desc: "row variable name" }, { name: "with", desc: "column variable name" }, { name: "r", desc: "correlation coefficient (number, [-1,1])" } ] } ] },
  metarow: { title: "Meta-analysis row", unit: "study", cardinality: "many", id: "id", label: "{id}",
    fields: [
      { name: "id", desc: "study label, e.g. 'Smith 2018'", type: "value", tab: "details" },
      { name: "yi", desc: "effect size on the pooled metric (number)", type: "value", tab: "details" },
      { name: "vi", desc: "variance of yi (number)", type: "value", tab: "details" },
      { name: "n", desc: "total sample size (integer or null)", type: "value", tab: "details" },
      { name: "design", desc: "RCT | cohort | cross-sectional | …", type: "value", tab: "details" } ] },
  pairwise: { title: "Pairwise correlation", unit: "effect size", cardinality: "many", id: "es_id", label: "{var1}–{var2}",
    fields: [
      { name: "es_id", desc: "1-indexed sequential integer", type: "value", tab: "details" },
      { name: "var1", desc: "canonical short name of first variable", type: "value", tab: "details" },
      { name: "var2", desc: "canonical short name of second variable", type: "value", tab: "details" },
      { name: "es", desc: "effect-size value (number)", type: "value", tab: "details" },
      { name: "type", desc: "r | d | OR | … (default r)", type: "value", tab: "details" },
      { name: "n", desc: "sample size for this correlation (integer or null)", type: "value", tab: "details" },
      { name: "reliabilities", desc: "per-variable reliability coefficients", type: "table", tab: "details",
        columns: [ { name: "variable", desc: "variable short name" }, { name: "alpha", desc: "reliability (Cronbach's α etc.), number or null" } ] } ] },
  scale: { title: "Scale / instrument", unit: "scale validation", cardinality: "one", id: "", label: "{scale_name}",
    fields: [
      { name: "scale_name", desc: "name of the instrument / scale", type: "value", tab: "details" },
      { name: "n_items", desc: "number of items (integer)", type: "value", tab: "details" },
      { name: "n_factors", desc: "number of factors / dimensions (integer)", type: "value", tab: "details" },
      { name: "factor_loadings", desc: "item × factor standardised loadings", type: "table", tab: "loadings",
        columns: [ { name: "item", desc: "item number or short text" }, { name: "factor", desc: "factor label, e.g. F1" }, { name: "loading", desc: "standardised loading (number or null)" } ] },
      { name: "cronbach_alpha", desc: "reliability of the full scale (number or null)", type: "value", tab: "details" } ] },
  custom: { title: "Other", unit: "", cardinality: "many", id: "", label: "", fields: [] },
};
function renderUnitPresets() {
  const box = $("#unit-presets"); if (!box) return;
  box.innerHTML = Object.entries(UNIT_PRESETS).map(([k, p]) =>
    `<button type="button" class="unit-chip${k === "custom" ? " other" : ""}" data-preset="${k}">${esc(p.title)}</button>`).join("");
  box.querySelectorAll(".unit-chip").forEach((b) => (b.onclick = () => applyUnitPreset(b.dataset.preset)));
}
function applyUnitPreset(key) {
  const p = UNIT_PRESETS[key]; if (!p) return;
  $("#sf-unit").value = p.unit || "";
  const card = document.querySelector(`input[name='sf-card'][value='${p.cardinality || "many"}']`); if (card) card.checked = true;
  $("#sf-id").value = p.id || "";
  $("#sf-label").value = p.label || "";
  TABS = [];
  (p.fields || []).forEach((f) => { const id = f.tab || "details"; if (!TABS.some((t) => t.id === id)) TABS.push({ id, label: cap(id) }); });
  if (!TABS.length) TABS = [{ id: "details", label: "Details" }];
  $("#sf-fields").innerHTML = "";
  (p.fields || []).forEach((f) => addField(f));
  if (!(p.fields || []).length) addField({});
  refreshTabSelects(); syncTabsUI(); updateUnitEcho();
  document.querySelectorAll(".unit-chip").forEach((b) => b.classList.toggle("sel", b.dataset.preset === key));
  // advanced: reveal the unit block + Next (stay on substep 1 until the user clicks Next)
  unitChosen = true;
  $("#unit-block").hidden = false;
  $("#sfNext").hidden = false;
  // guided: reveal the one-sentence + additional-info fields once a unit is chosen
  const rest = $("#simple-rest"); if (rest) rest.hidden = false;
  if (MODE === "advanced") requestAnimationFrame(() => $("#sfNext").scrollIntoView({ behavior: "smooth", block: "nearest" }));
}

// guided-mode unit chips (a simplified view over the same field state)
function renderSimpleUnits() {
  const box = $("#simple-units"); if (!box) return;
  box.innerHTML = Object.entries(UNIT_PRESETS).map(([k, p]) =>
    `<button type="button" class="unit-chip${k === "custom" ? " other" : ""}" data-preset="${k}">${esc(p.title)}</button>`).join("");
  box.querySelectorAll(".unit-chip").forEach((b) => (b.onclick = () => applyUnitPreset(b.dataset.preset)));
}

// guided-mode generate: reuse the chosen unit template's fields + a one-sentence
// context + free-text extra rules → the same assemblePrompt/buildFieldDefs machinery.
function genSimple() {
  if (!collectFields().length) { alert("Pick what you want to extract first."); return; }
  const desc = $("#simple-desc").value.trim();
  const extra = $("#simple-extra").value.trim();
  $("#sf-context").value = [desc && `We are studying: ${desc}.`, extra].filter(Boolean).join("\n");
  $("#prompt").value = assemblePrompt();
  lastFieldDefs = buildFieldDefs();
  try { window.__lastFieldDefs = lastFieldDefs; } catch { /* */ }
  summary(3, "Guided"); done(3); openStep(4);
}

// generation: prompt (sent) + field_defs (stashed for the deferred review UI) ─
function assemblePrompt() {
  const unit = $("#sf-unit").value.trim() || "record";
  const many = (document.querySelector("input[name='sf-card']:checked") || {}).value !== "one";
  const idField = $("#sf-id").value.trim();
  const fields = collectFields();
  const meta = parseFields($("#sf-meta").value);
  const ctx = $("#sf-context").value.trim();

  let p = `You are extracting data from an academic paper (PDF).\n\n`;
  p += `UNIT OF ANALYSIS: each "${unit}" — one row in the final dataset. `;
  p += many ? `A paper may report MANY; return a top-level array "records", one element per ${unit}.\n\n`
            : `There is exactly ONE per paper; still return a one-element "records" array.\n\n`;
  p += `For each ${unit} record, extract:\n`;
  fields.forEach((f) => {
    if (f.type === "list") {
      p += `  - "${f.name}": ${f.desc} — return a JSON array of scalar values.\n`;
    } else if (f.type === "table") {
      const cols = (f.columns || []).map((c) => `"${c.name}" (${c.desc})`).join(", ") || "the relevant columns";
      p += `  - "${f.name}": ${f.desc}. Return as {"_table": [ … ]} — one object per row, each row with keys: ${cols}.\n`;
    } else {
      p += `  - "${f.name}": ${f.desc}\n`;
    }
  });
  if (idField) p += `\nUse "${idField}" as each record's identifier (it becomes sample_id).\n`;
  p += `\nAlso return "paper_metadata" with: title, doi, year, authors, journal, volume, issue, pages`;
  if (meta.length) { p += `, plus:`; meta.forEach((f) => (p += `\n    - "${f.name}": ${f.desc}`)); }
  p += `.\n`;
  if (ctx) p += `\nADDITIONAL RULES:\n${ctx}\n`;
  const tableF = fields.find((f) => f.type === "table");
  const evField = tableF ? `records[0].${tableF.name}._table[0]` : fields[0] ? `records[0].${fields[0].name}` : `records[0]`;
  p += `\nReturn ONLY one JSON object — no prose, no markdown fences: { "records": [...], "paper_metadata": {...}, "evidence": [...] }.\n`;
  p += `EVIDENCE: a top-level array; each element has EXACTLY "snippet" (verbatim PDF text), "page" (1-indexed PDF page number), "source" (e.g. "Table 2" or null), "field" (JSON path, e.g. "${evField}"). Quote snippets character-for-character; never omit "page".`;
  return p;
}
function buildFieldDefs() {
  const fields = collectFields();
  const byTab = {};
  fields.forEach((f) => { (byTab[f.tab] ||= []).push(f.name); });
  const order = TABS.filter((t) => byTab[t.id]).map((t) => t.id);
  const used = order.length ? order : ["details"];
  const sub_views = used.map((id) => {
    const t = TABS.find((x) => x.id === id) || { id, label: cap(id) };
    return { id, label: t.label, include_keys: ["sample_id", ...(byTab[id] || [])],
             evidence_keys: byTab[id] || [], confidence_keys: byTab[id] || [] };
  });
  return {
    preset_id: null, mode: "extraction", sub_views,
    evidence_keys: [...new Set(sub_views.flatMap((s) => s.evidence_keys))].sort(),
    confidence_keys: [...new Set(sub_views.flatMap((s) => s.confidence_keys))].sort(),
    core_keys: [...new Set(fields.map((f) => f.name))].sort(),
    unit_label: $("#sf-unit").value.trim() || "record",
    sample_id_field: $("#sf-id").value.trim() || null,
    sidebar_label: $("#sf-label").value.trim() || null,
  };
}
function genStruct() {
  if (!collectFields().length) { alert("Add at least one field (or pick a unit template)."); openSub(2); return; }
  $("#prompt").value = assemblePrompt();
  lastFieldDefs = buildFieldDefs();
  try { window.__lastFieldDefs = lastFieldDefs; } catch { /* */ }
  summary(3, "Custom prompt (structured)"); done(3); openStep(4);
}
function usePaste() {
  const p = $("#pastebox").value.trim();
  if (!p) { alert("Write or paste your prompt."); return; }
  $("#prompt").value = p;
  summary(3, "Custom prompt"); done(3); openStep(4);
}

// ── step 5: upload (dropzone, multi-file) + run ─────────────────────────────
function setStatus(m) { $("#status").textContent = m; }
let FILES = [];

function setupDropzone() {
  const dz = $("#dropzone"), input = $("#pdf");
  dz.onclick = () => input.click();
  input.onchange = () => { FILES = [...input.files]; renderFiles(); };
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => {
    FILES = [...e.dataTransfer.files].filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    renderFiles();
  });
}
function renderFiles() {
  $("#filelist").innerHTML = FILES.map((f, i) =>
    `<div class="fileitem"><span>${esc(f.name)}</span><span class="fstatus" id="fst-${i}">ready</span></div>`).join("");
}

let STATUS = [];   // per FILES index: { state:"pending"|"extracting"|"done"|"failed", res?, error? }

// The schema id for the current task (also the recipe's schema leg when saving).
function schemaIdFor() {
  return task === "workflow" ? `${presetId}@v1`
    : task === "summarise" ? "summarize@v1" : `${task}@v1`;
}

async function run() { STATUS = FILES.map(() => ({ state: "pending" })); await runBatch(FILES.map((_, i) => i), true); }

const MAX_CONCURRENT = 4;   // simultaneous extractions (respect provider rate limits)

async function runBatch(indices, reset) {
  if (!FILES.length) return setStatus("add at least one PDF");
  if (!STATUS.length) STATUS = FILES.map(() => ({ state: "pending" }));
  indices.forEach((i) => (STATUS[i] = { state: "pending" }));   // retried ones go back to pending
  if (reset) $("#result").innerHTML = "";
  $("#run").disabled = true; setStatus(`extracting ${indices.length} paper(s)…`);
  setNav("results"); $("#result").scrollIntoView({ behavior: "smooth" });
  const schemaId = ADD_DATASET ? (ADD_DATASET.schema_id || schemaIdFor()) : schemaIdFor();
  const runStart = new Date().toISOString();
  let forwarded = false, anyQueued = false;
  const jobIds = [];                        // queued jobs of this round → tracked in review

  // As soon as the first paper lands, jump to the review panel scoped to this round.
  // ?since picks up siblings' documents as they land; ?jobs lets the panel show each
  // still-running paper as "extracting…" and each failure as an error (queued jobs
  // survive navigation on the worker).
  function forwardUrl() {
    const q = jobIds.length ? `&jobs=${encodeURIComponent(jobIds.join(","))}` : "";
    return `/workspace?since=${encodeURIComponent(runStart)}${q}`;
  }
  function maybeForward() {
    if (forwarded || !reset || ADD_DATASET) return;
    if (!anyQueued) return;                 // sync mode: don't abort in-flight siblings
    forwarded = true;
    location.href = forwardUrl();
  }

  // worker pool: pull indices off a shared cursor, up to MAX_CONCURRENT at once
  let ptr = 0;
  async function worker() {
    while (ptr < indices.length) {
      const i = indices[ptr++];
      STATUS[i] = { state: "extracting" }; renderResults();
      const fd = new FormData();
      fd.append("pdf", FILES[i]);
      fd.append("prompt", $("#prompt").value);
      fd.append("schema_id", schemaId);
      if (USE_CREDITS) {
        fd.append("use_credits", "true");        // server supplies the model + its own key
      } else {
        fd.append("model", $("#model").value);
        fd.append("api_key", $("#apikey").value);
      }
      try {
        const data = await api.extract(fd);
        if (data.queued) { anyQueued = true; if (data.job_id) jobIds.push(data.job_id); }
        const res = data.queued ? await pollJob(data.job_id) : data;
        STATUS[i] = { state: "done", res }; renderResults();
        maybeForward();
      } catch (e) { STATUS[i] = { state: "failed", error: e.message }; renderResults(); }
    }
  }
  await Promise.all(Array.from({ length: Math.min(MAX_CONCURRENT, indices.length) }, worker));

  $("#run").disabled = false; setStatus("done");
  renderResults(true);
  // sync mode (jobs don't survive navigation): forward only once the batch is complete
  if (reset && !ADD_DATASET && !forwarded && STATUS.some((s) => s.state === "done")) {
    location.href = forwardUrl();
  }
}

async function pollJob(jobId) {
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    let st; try { st = await api.job(jobId); } catch { continue; }
    if (st.success === true) return st.result;
    if (st.success === false) throw new Error(st.error || "extraction failed");
  }
  throw new Error("timed out — is the worker running?");
}

// Live results list: every paper with a status — a spinner while extracting, a
// record count + Open link when done, or the error + a Retry-failed button.
function renderResults(finished = false) {
  const done = STATUS.filter((s) => s.state === "done");
  // Review from the results scopes the workspace to THIS round (or the dataset when
  // adding papers) — so you only see the current extraction, not all your history.
  const doneIds = done.map((s) => s.res.document_id);
  const scopeQ = ADD_DATASET ? `project=${esc(ADD_DATASET.id)}`
    : (doneIds.length ? `docs=${doneIds.map(esc).join(",")}` : "");
  const failedIdx = STATUS.map((s, i) => (s.state === "failed" ? i : -1)).filter((i) => i >= 0);
  const head = finished
    ? `✓ Extracted ${done.length} of ${FILES.length}${failedIdx.length ? ` · ${failedIdx.length} failed` : ""}`
    : `Extracting… ${done.length}/${FILES.length} done${failedIdx.length ? ` · ${failedIdx.length} failed` : ""}`;
  const rows = FILES.map((f, i) => {
    const s = STATUS[i] || { state: "pending" };
    let right;
    if (s.state === "done") right = `<span class="fi-ok">✓ ${s.res.n_records} rec</span> <a href="/workspace?${scopeQ}${scopeQ ? "&" : ""}doc=${esc(s.res.document_id)}">Open →</a>`;
    else if (s.state === "failed") right = `<span class="fstatus">✗ ${esc(s.error || "failed")}</span>`;
    else right = `<span class="spin"></span> <span class="muted">${s.state === "extracting" ? "extracting…" : "queued"}</span>`;
    return `<div class="fileitem"><span>${esc(f.name)}</span><span class="fi-right">${right}</span></div>`;
  }).join("");
  const doneItems = STATUS.map((s, i) => ({ s, i })).filter((x) => x.s.state === "done")
    .map((x) => ({ name: FILES[x.i].name, res: x.s.res }));
  const actions = [];
  if (failedIdx.length && finished) actions.push(`<button class="btn btn-ghost" id="retryfailed">🔁 Retry failed (${failedIdx.length})</button>`);
  if (doneItems.length) actions.push(`<button class="btn btn-primary" id="saveproj">${ADD_DATASET ? `＋ Add to “${esc(ADD_DATASET.title)}”` : "💾 Save to my workspace"}</button>`
    + `<span id="savemsg" class="muted" style="align-self:center"></span>`);
  $("#result").innerHTML = `<div class="card" style="margin-top:16px">`
    + `<div style="font-weight:600;margin-bottom:8px">${head}</div>` + rows
    + (actions.length ? `<div class="wf-actions" style="margin-top:12px;justify-content:flex-start">${actions.join("")}</div>` : "")
    + `</div>`;
  if (doneItems.length) $("#saveproj").onclick = () => doSave(doneItems);
  if (failedIdx.length && finished) $("#retryfailed").onclick = () => runBatch(failedIdx, false);
}

async function doSave(out) {
  const btn = $("#saveproj"), msg = $("#savemsg");
  btn.disabled = true; msg.textContent = "";
  const docIds = out.map((r) => r.res.document_id);
  try {
    // add-papers mode: assign to the existing dataset (no new dataset), then return to it
    if (ADD_DATASET) {
      for (const id of docIds) await api.addToDataset(ADD_DATASET.id, { document_id: id });
      msg.textContent = `✓ added ${docIds.length} to "${ADD_DATASET.title}"`; btn.textContent = "✓ Added";
      setTimeout(() => { location.href = `/dataset?id=${ADD_DATASET.id}`; }, 900);
      return;
    }
    // Capture the round's recipe onto the new dataset (its default for adding papers).
    const recipe = { prompt: $("#prompt").value, model: $("#model").value, schema_id: schemaIdFor() };
    const ds = await saveToWorkspace(docIds,
      { defaultName: (out[0].name || "").replace(/\.pdf$/i, ""), recipe });
    if (ds) {
      msg.innerHTML = `✓ saved to "${esc(ds.title)}" (${esc(ds.visibility)}) · `
        + `<a href="/workspace?project=${esc(ds.id)}">Review data →</a>`;
      btn.textContent = "✓ Saved";
    }
  } catch (e) { msg.textContent = "✗ " + e.message; }
  finally { btn.disabled = false; }
}

init();
