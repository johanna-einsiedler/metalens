// Extraction workflow — sidebar nav + progressive-disclosure accordion.
// Steps fold by default and any can be opened by clicking (header or sidebar);
// completing a step opens the next. Step 2 mirrors the old version: provider →
// model (from models.json) → API key + test connection.
import { api } from "/static/api.js";
import { esc, renderMarkdown } from "/static/grammar.js";
import { saveToWorkspace } from "/static/save.js";
import { getKey, setKey } from "/static/keys.js";

const $ = (s) => document.querySelector(s);
let task = null;          // extract | label | summarise | workflow
let presetId = null;
let presets = [];
let SETUP = null;    // the saved setup (sub-preset) the builder was opened with, if any
let MODELS = {};
let ADD_DATASET = null;   // {id,title,schema_id,prompt,model} when ?dataset= (add-papers mode)
let USE_CREDITS = false;  // logged-in keyless run on Metalens's server key + fixed model
let CFG = null;           // /api/extraction-config: default model + logged-out limits
let FILE_CAP = null;      // null = no cap (logged in, or own key); a number = papers still allowed

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
  // Add-papers (?dataset=…): jump straight to Upload & extract and wire the dropzone NOW,
  // before the slow model/preset load — so the user lands on drag-and-drop instead of
  // seeing step 1 ("turn a paper into data") flash for a second first.
  const addingTo = new URLSearchParams(location.search).get("dataset");
  setupDropzone();
  if (addingTo) openStep(4);
  try { presets = (await api.presets()).presets || []; } catch { /* */ }
  await loadModels();
  document.querySelectorAll(".task-card[data-task]").forEach((c) =>
    (c.onclick = () => {
      if (c.classList.contains("soon")) return;   // not shipping yet — the card says so
      selectTask(c.dataset.task, c);
    }));
  // any step / sidebar item can be unfolded by clicking
  steps.forEach((s) => (s.querySelector(".acc-head").onclick = () => openStep(+s.dataset.step)));
  document.querySelectorAll(".wf-step-nav").forEach((w) => (w.onclick = () => {
    if (w.dataset.step === "results") { setNav("results"); $("#result").scrollIntoView({ behavior: "smooth" }); return; }
    openStep(+w.dataset.step);
  }));
  document.querySelectorAll("[data-back]").forEach((b) => (b.onclick = () => openStep(+b.dataset.back)));
  $("#next4").onclick = () => { done(3); summary(3, modelSummary()); openStep(4); };
  $("#ml-toggle").onclick = toggleModelPanel;
  $("#testconn").onclick = testConnection;
  $("#apikey").oninput = () => { setKey($("#provider").value, $("#apikey").value.trim()); applyKeyMode(); };
  $("#model").addEventListener("change", applyKeyMode);
  $("#run").onclick = run;
  // step 3: structured designer (substeps) + freeform toggle
  document.querySelectorAll(".sf-pill").forEach((p) => (p.onclick = () => openSub(+p.dataset.sub)));
  $("#sfNext").onclick = () => (sub < 4 ? openSub(sub + 1) : genStruct());
  $("#sfBack").onclick = () => (sub === 1 ? openStep(1) : openSub(sub - 1));
  $("#addfield").onclick = () => addField({});
  $("#addtab").onclick = () => addTab();
  $("#tabsadd").onclick = () => addTab();
  $("#sf-unit").oninput = updateUnitEcho;
  $("#usepaste").onclick = usePaste;
  document.querySelectorAll(".mode-btn").forEach((b) => (b.onclick = () => showMode(b.dataset.mode)));
  $("#simpleGen").onclick = genSimple;
  $("#masemUse").onclick = masemUse;
  $("#masemSetupSave").onclick = saveMasemSetup;
  ["masemEffectSizes", "masemVariables", "masemScaleName", "masemNItems", "masemItems"].forEach((id) => {
    const el = $("#" + id); if (el) { el.addEventListener("input", refreshMasemPreview); el.addEventListener("change", refreshMasemPreview); }
  });
  renderUnitPresets();
  renderSimpleUnits();
  syncTabsUI();
  setupTooltips();
  await setupExtractionConfig();  // resolve model + credits first so add-papers can reflect them
  await maybeAddPapersMode();
}

// The model line under the prompt: which model runs this, how much the user has left,
// and the way out. The server decides the model — the browser only reports it — so the
// provider/model/key controls stay folded away until someone asks for them.
async function setupExtractionConfig() {
  try { CFG = await api.extractionConfig(); } catch { CFG = null; }
  const bal = CFG && CFG.credits ? (CFG.credits.balance || 0) : 0;
  // Credits are the default whenever the user is logged in and actually has some.
  USE_CREDITS = !!(CFG && CFG.logged_in && CFG.offered && bal > 0);

  const nameEl = $("#ml-model");
  if (nameEl) nameEl.textContent = (CFG && CFG.model) || "not configured";

  const cr = $("#ml-credits");
  if (cr && CFG && CFG.logged_in) {
    cr.textContent = bal === 1 ? "1 credit left" : `${bal} credits left`;
    cr.classList.toggle("ml-warn", bal <= 0);
    cr.hidden = false;
  }

  // Logged out: one free paper, and no export. Say both up front rather than failing later.
  const an = $("#ml-anon");
  if (an && CFG && !CFG.logged_in) {
    const left = Math.max(0, (CFG.anon_free_extractions || 0) - (CFG.anon_extractions_used || 0));
    an.innerHTML = left > 0
      ? `${left} free paper${left === 1 ? "" : "s"} without an account · `
        + `<a href="/account">create one</a> to extract more and download results`
      : `Free trial used · <a href="/account">create a free account</a> to keep extracting, `
        + `or add your own API key below`;
    an.hidden = false;
  }

  // The credits/own-key radio only means anything when there are credits to choose.
  const sw = $("#credit-switch");
  if (sw && CFG && CFG.logged_in && CFG.offered && bal > 0) {
    $("#credit-left").textContent = `(${bal} left)`;
    if (CFG.model) $("#credit-model").textContent = `· ${CFG.model}`;
    sw.hidden = false;
    sw.querySelectorAll('input[name="keymode"]').forEach((r) => (r.onchange = () => {
      USE_CREDITS = sw.querySelector('input[name="keymode"]:checked').value === "credits";
      applyKeyMode();
    }));
  }
  applyKeyMode();
}

// A typed key overrides everything — including the logged-out cap, since the run no
// longer costs us anything.
function ownKeyPresent() { const k = $("#apikey"); return !!(k && k.value.trim()); }
// The logged-out cap, recomputed from scratch each time: a typed key lifts it, and
// CLEARING that key has to put it back (otherwise one keystroke buys unlimited runs).
function anonCap() {
  if (!CFG || CFG.logged_in || ownKeyPresent()) return null;   // no cap at all
  return Math.max(0, (CFG.anon_free_extractions || 0) - (CFG.anon_extractions_used || 0));
}
function applyKeyMode() {
  const fields = $("#ownkey-fields"); if (fields) fields.hidden = USE_CREDITS;
  FILE_CAP = anonCap();
  const nameEl = $("#ml-model");
  if (nameEl) {
    nameEl.textContent = (!USE_CREDITS && ownKeyPresent() && $("#model").value)
      ? $("#model").value : ((CFG && CFG.model) || "not configured");
  }
}
function toggleModelPanel() {
  const block = $("#ownkey-block"), btn = $("#ml-toggle"); if (!block || !btn) return;
  const open = block.hidden;
  block.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
  btn.querySelector(".ml-chev").textContent = open ? "▴" : "▾";
}
function modelSummary() {
  if (!USE_CREDITS && ownKeyPresent() && $("#model").value) return `${$("#model").value} · your key`;
  return (CFG && CFG.model) || "default model";
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
  done(2); summary(2, "reusing saved prompt");
  done(3); summary(3, modelSummary());
  openStep(4);
  const body = stepEl(4).querySelector(".acc-body");
  if (body && !document.querySelector("#addbanner")) {
    const b = document.createElement("div");
    b.id = "addbanner"; b.className = "add-banner";
    b.innerHTML = `Adding papers to <b>${esc(ADD_DATASET.title)}</b> — drop your PDFs below and Run. `
      + `They reuse this dataset’s model, prompt &amp; schema; open a step above to change any before running. `
      + `<a href="/dataset?id=${esc(dsId)}">Back to dataset →</a>`;
    body.prepend(b);
  }
  // came from "All my papers → Add to dataset": pre-load the cached PDF so the user just
  // hits Run (re-extracted here with THIS dataset's recipe, no re-upload needed).
  const source = new URLSearchParams(location.search).get("source");
  if (source) await loadSourcePaper(source, new URLSearchParams(location.search).get("name"));
}

// Fetch an already-cached PDF by its document id (owner-gated /artifacts route) and stage
// it as the file to extract — used when re-extracting a library paper into a dataset.
async function loadSourcePaper(docId, name) {
  try {
    const r = await fetch(`/artifacts/pdf/${encodeURIComponent(docId)}.pdf`, { credentials: "same-origin" });
    if (!r.ok) throw new Error(`status ${r.status}`);
    const blob = await r.blob();
    const fname = (name && name.trim()) || `${docId}.pdf`;
    FILES = [new File([blob], fname, { type: "application/pdf" })];
    renderFiles();
  } catch (e) {
    setStatus(`Couldn't load the paper's cached PDF (${e.message}). Drop the file manually to continue.`);
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
    const items = presets.filter((p) => p.mode === "extraction" && !p.setup);
    const setups = presets.filter((p) => p.setup);
    // a saved setup sits under the card of its base; the hidden MASEMiner variant's setups
    // join the visible MASEMiner card (the builder switches variant on its own)
    const setupsOf = (base) => setups.filter((s) => s.base_preset_id === base.preset_id
      || (isMasemPreset(s.base_preset_id) && isMasemPreset(base.preset_id)));
    $("#taskdetail").innerHTML =
      `<div class="muted" style="font-size:13px;margin:6px 0 8px">Pick a pre-built method — a complete, tested prompt that skips the prompt-design step:</div>`
      + `<div class="method-grid">` + (items.map((p) =>
          `<button type="button" class="method-card" data-pid="${esc(p.preset_id)}">`
          + `<span class="mc-title">${esc(p.title)}`
          + (p.personal ? ` <span class="mc-badge">${p.owned ? "Personal" : "Shared"}</span>` : "")
          + `</span>`
          + `<span class="mc-sub">${esc(p.tagline || "")}</span></button>`
          + setupsOf(p).map((s) =>
            `<button type="button" class="method-card sub" data-pid="${esc(s.base_preset_id)}" data-setup="${esc(s.preset_id)}" data-title="${esc(p.title)}">`
            + `<span class="mc-title">${esc(p.title)} · ${esc(s.title)} <span class="mc-badge">Setup</span></span>`
            + `<span class="mc-sub">${esc(setupSummary(s))}</span></button>`).join("")).join("")
          || '<span class="muted">No pre-built methods available.</span>')
      + `</div>`;
    $("#taskdetail").querySelectorAll(".method-card").forEach((b) => (b.onclick = () => {
      presetId = b.dataset.pid;
      SETUP = b.dataset.setup ? setups.find((s) => s.preset_id === b.dataset.setup) || null : null;
      document.querySelectorAll(".method-card").forEach((x) => x.classList.toggle("sel", x === b));
      const p = presets.find((x) => x.preset_id === presetId);
      const title = b.dataset.title || (p ? p.title : presetId);   // a setup's base may be a hidden variant
      advance(`Workflow: ${title}${SETUP ? " · " + SETUP.title : ""}`);
    }));
  } else {
    $("#taskdetail").innerHTML = "";
    advance({ extract: "Extract data", label: "Label a paper", summarise: "Summarise a paper" }[t]);
  }
}
// one line that says what a saved setup pins (the picker's sub-card)
function setupSummary(s) {
  const p = s.params || {};
  if (p.scale_name || p.n_items || (p.item_texts || []).length) {
    return [p.scale_name, p.n_items ? `${p.n_items} items` : "", (p.item_texts || []).length ? "item texts" : ""].filter(Boolean).join(" · ");
  }
  const es = (p.effect_sizes || []).map((e) => (typeof e === "string" ? e : e.code)).filter(Boolean);
  const vs = (p.variables || []).map((v) => v && v.name).filter(Boolean);
  return [es.length ? `effect sizes: ${es.join(", ")}` : "", vs.length ? `variables: ${vs.join(", ")}` : ""].filter(Boolean).join(" · ") || "saved setup";
}

// ── step 1 → 2 (describe the task) or straight to 3 when the prompt is pre-built ──
// The old step 2 (pick a provider/model/key) is gone: extraction runs on the server's
// default model, and bringing your own key is a disclosure inside step 3.
async function advance(sum) {
  done(1); summary(1, sum);
  if (task === "extract" || task === "label") {
    showMode("simple");
    openStep(2);
  } else if (isMasemPreset(presetId)) {
    await openMasemBuilder(presetId);   // guided MASEMiner builder (Direct/Indirect)
    openStep(2);
  } else {
    summary(2, "auto (pre-built prompt)"); done(2);
    const pid = task === "summarise" ? "summarize" : presetId;
    if (pid) {
      try { PROMPT_RENDERED = (await api.presetPrompt(pid)).prompt; $("#prompt").value = PROMPT_RENDERED; }
      catch { PROMPT_RENDERED = ""; $("#prompt").value = ""; }
    }
    openStep(3);
  }
}

// ── step 3: structured "describe what to extract" designer (substeps) ───────
let sub = 1;
let unitChosen = false;   // substep 1: unit block + Next stay hidden until a template is picked
// The declarative preset the Guided/Advanced designer produced for this run, the prompt the
// SERVER rendered from it (so an edit in step 3 is detectable), the personal preset it was
// saved as before running, and the schema id the server minted for the round.
let RUN_SPEC = null, PROMPT_RENDERED = "", RUN_PRESET_ID = null, LAST_SCHEMA_ID = null;
const PRESET_CACHE = {};  // spec JSON → personal preset id (a re-run of the same design reuses it)
const promptEdited = () => $("#prompt").value.trim() !== (PROMPT_RENDERED || "").trim();
// step 3 has three levels: guided (default) · advanced (structured designer) · own prompt
let MODE = "simple";
function showMode(m) {
  // A pre-built method owns the whole "describe your task" step — its builder IS the
  // description. Never let a stray Guided/Advanced click swap it for the generic form
  // (the mode switch is hidden for presets, but re-entering the step must be idempotent).
  if (isMasemPreset(presetId)) { openMasemBuilder(presetId); return; }
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
  { id: "masem-direct", label: "Direct information", tag: "The paper reports the effect sizes themselves — correlations in text or table(s)." },
  { id: "masem-indirect", label: "Indirect information", tag: "The paper reports the measurement model — factor loadings and factor correlations." },
];

async function openMasemBuilder(pid) {
  const ms = document.querySelector(".mode-switch"); if (ms) ms.hidden = true;
  $("#simpleform").hidden = true; $("#structform").hidden = true; $("#pasteflow").hidden = true;
  $("#masemBuilder").hidden = false;
  renderMasemStarters();
  await selectMasemStarter(isMasemPreset(pid) ? pid : "masem-direct", false);
  if (SETUP && SETUP.base_preset_id === MASEM.starter) {      // a saved setup: its values, editable
    fillMasemValues(SETUP.params || {});
    $("#masemSetupName").value = SETUP.title;
    $("#masemSetupStatus").textContent = "loaded — edit and save under the same name to update";
    await doMasemPreview();
  } else {
    $("#masemSetupName").value = ""; $("#masemSetupStatus").textContent = "";
  }
}
// the form's values (not placeholders) from a saved setup's parameters
function fillMasemValues(p) {
  const es = $("#masemEffectSizes"); if (es) es.value = serialiseEffectSizes(p.effect_sizes || []);
  const vs = $("#masemVariables"); if (vs) vs.value = serialiseVariables(p.variables || []);
  const sn = $("#masemScaleName"); if (sn) sn.value = (p.scale_name && p.scale_name !== "the target instrument") ? p.scale_name : "";
  const ni = $("#masemNItems"); if (ni) ni.value = p.n_items || "";
  const it = $("#masemItems"); if (it) it.value = (p.item_texts || []).map((t, i) => `${i + 1}: ${t}`).join("\n");
}
// "Save this setup": the builder's values become a private sub-preset of the current variant;
// saving under a loaded setup's own name updates it instead
async function saveMasemSetup() {
  const name = ($("#masemSetupName").value || "").trim();
  const st = $("#masemSetupStatus");
  if (!name) { st.textContent = "give the setup a name first"; $("#masemSetupName").focus(); return; }
  // only the parameters the preset declares (the form also sends a few historical aliases)
  const raw = readMasemParams();
  const params = Object.fromEntries(Object.entries(raw).filter(([k]) => k in (MASEM.defaults || {})));
  st.textContent = "saving…";
  try {
    if (SETUP && SETUP.base_preset_id === MASEM.starter && SETUP.title === name) {
      const r = await api.updatePreset(SETUP.preset_id, { params });
      SETUP.params = r.params || params;
      const row = presets.find((x) => x.preset_id === SETUP.preset_id); if (row) row.params = SETUP.params;
      st.textContent = "updated";
    } else {
      const r = await api.createPreset({ title: name, base_preset_id: MASEM.starter, params, visibility: "private" });
      SETUP = { preset_id: r.id, title: r.title, tagline: r.tagline, mode: "extraction", setup: true,
                base_preset_id: MASEM.starter, params: r.params || params, personal: true, owned: true };
      presets.push(SETUP);
      st.textContent = "saved — it now appears under MASEMiner in step 1";
    }
  } catch (e) { st.textContent = "could not save: " + e.message; }
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
  if (isUserClick && SETUP && SETUP.base_preset_id !== pid) {   // a setup belongs to one variant
    SETUP = null; $("#masemSetupName").value = ""; $("#masemSetupStatus").textContent = "";
  }
  let detail = MASEM.cache[pid];
  if (!detail) { try { detail = await api.presetDetail(pid); MASEM.cache[pid] = detail; } catch { return; } }
  MASEM.starter = pid;
  presetId = pid;                       // schemaIdFor() → `${pid}@v1` (masem-direct / masem-indirect)
  MASEM.defaults = JSON.parse(JSON.stringify(detail.template_params || {}));
  renderMasemStarters();
  const direct = pid === "masem-direct";
  $("#masemFormDirect").hidden = !direct;
  $("#masemFormIndirect").hidden = direct;
  populateMasemForm(MASEM.defaults);
  if (MASEM.timer) { clearTimeout(MASEM.timer); MASEM.timer = null; }
  await doMasemPreview();
}
function populateMasemForm(d) {
  const es = $("#masemEffectSizes"); if (es) { es.value = ""; es.placeholder = serialiseEffectSizes(d.effect_sizes) || "r: Correlation\nor: Odds ratios"; }
  const vs = $("#masemVariables"); if (vs) { vs.value = ""; vs.placeholder = serialiseVariables(d.variables) || "bm: A measure of body mass such as BMI or waist circumference\nvg: A measure of video-game use — hours/day or session frequency\npa: A measure of physical activity — exercise length or frequency"; }
  const sn = $("#masemScaleName"); if (sn) { sn.value = ""; const nm = d.scale_name || d.instrument_name; sn.placeholder = (nm && nm !== "the target scale" && nm !== "the target instrument") ? `e.g. ${nm}` : "the scale this extraction targets"; }
  const ni = $("#masemNItems"); if (ni) ni.value = "";
  const it = $("#masemItems"); if (it) {
    it.value = ""; const items = Array.isArray(d.item_texts) ? d.item_texts : [];
    it.placeholder = items.length ? items.slice(0, 3).map((t, i) => `${i + 1}: ${t}`).join("\n") + (items.length > 3 ? `\n…  (${items.length - 3} more example items used by default)` : "") : "1: <first item text>\n2: <second item text>\n3: <third item text>\n…";
  }
}
function readMasemParams() {
  const p = {};
  if (MASEM.starter === "masem-direct") {
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
    const md = r.prompt || "";
    PROMPT_RENDERED = md;
    $("#masemPreviewBox").innerHTML = renderMarkdown(md);
    $("#masemPreviewLen").textContent = md.length;
    $("#prompt").value = md;               // the RAW markdown is what the model gets
  } catch { /* best-effort preview */ }
}
function refreshMasemPreview() { if (MASEM.timer) clearTimeout(MASEM.timer); MASEM.timer = setTimeout(doMasemPreview, 350); }
async function masemUse() {
  await doMasemPreview();
  if (!$("#prompt").value.trim()) { alert("Could not build a prompt — fill in the fields."); return; }
  summary(2, `MASEMiner · ${MASEM.starter === "masem-direct" ? "direct" : "indirect"}`); done(2); openStep(3);
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
  papermeta: { title: "Paper metadata", unit: "paper", cardinality: "one", id: "", label: "{title}", fields: [] },
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

// Guided mode deliberately shows FOUR choices over the same UNIT_PRESETS state — the
// full seven live in Advanced. "Something else" carries no field template, so its
// one-sentence description is the only spec the prompt gets, and is required.
const SIMPLE_UNITS = [
  { key: "regression", label: "Regression results",       tag: "One row per regression / model in a results table." },
  { key: "papermeta",  label: "Paper metadata",           tag: "One row per paper — the bibliographic details only." },
  { key: "finding",    label: "Findings & effect sizes",  tag: "One row per reported finding or effect size." },
  { key: "custom",     label: "Something else",           tag: "Describe it yourself in one sentence." },
];
const descRequired = () => SIMPLE_UNIT === "custom";
let SIMPLE_UNIT = null;

function renderSimpleUnits() {
  const box = $("#simple-units"); if (!box) return;
  box.innerHTML = SIMPLE_UNITS.map((u) =>
    `<button type="button" class="unit-chip${u.key === "custom" ? " other" : ""}" data-preset="${u.key}" `
    + `title="${esc(u.tag)}">${esc(u.label)}</button>`).join("");
  box.querySelectorAll(".unit-chip").forEach((b) => (b.onclick = () => {
    SIMPLE_UNIT = b.dataset.preset;
    applyUnitPreset(SIMPLE_UNIT);
    // the description is the whole spec for "Something else" — mark it required
    $("#simple-desc-req").textContent = descRequired() ? "(required)" : "(optional)";
    $("#simple-desc-help").hidden = !descRequired();
    $("#simple-desc").placeholder = descRequired()
      ? "e.g. one row per robustness check, with its specification and coefficient"
      : "e.g. the effect of remote work on productivity";
  }));
}

// guided-mode generate: reuse the chosen unit template's fields + a one-sentence
// context + free-text extra rules → the same buildSpec / server-rendered prompt machinery.
function genSimple() {
  if (!SIMPLE_UNIT) { alert("Pick what you want to extract first."); return; }
  const desc = $("#simple-desc").value.trim();
  if (descRequired() && !desc) {
    $("#simple-desc-help").hidden = false;
    $("#simple-desc").focus();
    return;
  }
  // "Something else" ships no field template, so the sentence IS the field spec.
  $("#sf-context").value = desc
    ? (descRequired() ? `Extract one record per: ${desc}.` : `We are studying: ${desc}.`)
    : "";
  commitSpec("Guided");
}

// ── designer → declarative preset (format 2) ─────────────────────────────────
// The form is translated into the same document a file preset is written in; the SERVER
// validates it, renders the prompt (task text + generated schema/evidence/confidence
// sections) and, at run time, saves it as a personal preset so the review UI knows the
// fields, tabs and confidence groups. Nothing about the layout is decided in the browser.
const SPEC_TYPE_WORDS = [
  [/\b(integer|count|sample size|1-indexed|sequential)\b/i, "integer"],
  [/\b(number|numeric|coefficient|estimate|p-value|standard error|std\.? ?error|loading|correlation|variance|bound|value\b.*\bnumber)/i, "number"],
  [/\b(true|false|boolean|yes\/no)\b/i, "boolean"],
];
function specScalarType(desc) {
  for (const [re, t] of SPEC_TYPE_WORDS) if (re.test(desc || "")) return t;
  return "string";
}
function specField(f) {
  const out = { name: f.name, label: f.name.replace(/_/g, " ") };
  if (f.desc) out.help = f.desc;
  if (f.type === "list") out.type = "list";
  else if (f.type === "table") {
    out.type = "table";
    out.columns = (f.columns || []).map((c) => {
      const col = { name: c.name, type: specScalarType(c.desc) };
      if (c.desc) col.help = c.desc;
      return col;
    });
    if (!out.columns.length) out.columns = [{ name: "value", type: "string" }];
  } else out.type = specScalarType(f.desc);
  return out;
}
// a literal "$" in author text would read as a template placeholder server-side
const specText = (t) => String(t || "").replace(/\$/g, "$$$$");
function buildSpec() {
  const guided = MODE === "simple";
  const unitKey = guided ? SIMPLE_UNIT : null;
  const unit = $("#sf-unit").value.trim() || "record";
  const many = (document.querySelector("input[name='sf-card']:checked") || {}).value !== "one";
  const fields = collectFields();
  const names = new Set(fields.map((f) => f.name));
  const meta = parseFields($("#sf-meta").value);
  const ctx = $("#sf-context").value.trim();
  const desc = guided ? $("#simple-desc").value.trim() : "";
  const simpleUnit = SIMPLE_UNITS.find((u) => u.key === unitKey);

  let text = `# TASK\nYou are extracting data from an academic paper (PDF).\n\n`;
  text += `UNIT OF ANALYSIS: each "${specText(unit)}" — one row in the final dataset. `;
  text += many ? `A paper may report MANY; return one element per ${specText(unit)}.\n`
               : `There is exactly ONE per paper; still return a one-element array.\n`;
  // Two different reasons a unit can carry no field list, and they need opposite prompts:
  // "Paper metadata" genuinely has none (everything lives in paper_metadata), whereas
  // "Something else" has none YET — its one-sentence description is the spec, so the model
  // has to derive the fields and the schema section is left out rather than shown empty.
  if (!fields.length && unitKey === "papermeta") {
    text += `This record carries NO fields of its own — everything of interest belongs in `
      + `"paper_metadata" (see PAPER METADATA below). Return a single empty record.\n`;
  } else if (!fields.length) {
    text += `Work out which fields each ${specText(unit)} needs from the description and the rules `
      + `below. Choose concise snake_case keys, keep every value a scalar (string, number, `
      + `boolean or null), and use the SAME keys for every record.\n`;
  }
  if (ctx) text += `\nADDITIONAL RULES:\n${specText(ctx)}\n`;

  const spec = {
    format: 2,
    meta: {
      title: guided ? `Guided · ${simpleUnit ? simpleUnit.label : cap(unit)}` : `Custom · ${cap(unit)}`,
      tagline: (desc || ctx || `One row per ${unit}.`).slice(0, 200),
      mode: "extraction",
    },
    prompt: { text, generate: fields.length
      ? ["paper_metadata", "output_schema", "evidence", "confidence", "return_format"]
      : ["paper_metadata", "evidence", "confidence", "return_format"] },
    paper: { fields: meta.filter((f) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(f.name)).map((f) => specField({ ...f, type: "value" })) },
    entries: { key: "records", label: cap(unit), cardinality: many ? "many" : "one",
               fields: fields.map(specField) },
    confidence: { levels: ["high", "medium", "low"], notes: true, groups: [] },
    display: { entries: "cards", triage: "low_confidence_first" },
  };
  const idField = $("#sf-id").value.trim();
  if (idField && names.has(idField)) spec.entries.id_field = idField;
  const title = $("#sf-label").value.trim();
  if (title && [...title.matchAll(/\{([A-Za-z_#][A-Za-z0-9_]*)\}/g)].every((m) => names.has(m[1]))) spec.entries.title = title;
  // review tabs → display tabs, each with its own confidence group (the model rates the
  // tab's fields together, which is how a coder checks them)
  const byTab = {};
  fields.forEach((f) => { (byTab[f.tab] ||= []).push(f.name); });
  const tabs = TABS.filter((t) => byTab[t.id]);
  if (tabs.length > 1) spec.display.tabs = tabs.map((t) => ({ id: t.id, label: t.label, fields: byTab[t.id] }));
  if (fields.length) {
    spec.confidence.groups = tabs.length > 1
      ? tabs.map((t) => ({ id: t.id, label: t.label, scope: "entry", help: `the ${t.label} fields of this ${unit}` }))
      : [{ id: "record", label: cap(unit), scope: "entry", help: `all fields of this ${unit}` }];
    const groupOf = {}; tabs.forEach((t) => byTab[t.id].forEach((n) => (groupOf[n] = t.id)));
    spec.entries.fields.forEach((f) => (f.confidence = tabs.length > 1 ? (groupOf[f.name] || tabs[0].id) : "record"));
  }
  if (spec.paper.fields.length) {
    spec.confidence.groups.push({ id: "paper_fields", label: "Paper details", scope: "paper" });
    spec.paper.fields.forEach((f) => (f.confidence = "paper_fields"));
  }
  return spec;
}
// Validate + render server-side, then move on to step 3 with the rendered prompt.
async function commitSpec(label) {
  const spec = buildSpec();
  let r;
  try { r = await api.validatePreset(spec); }
  catch (e) { alert("Could not build the prompt: " + e.message); return; }
  if (!r.ok) { alert("The design has problems:\n\n" + (r.errors || []).join("\n")); return; }
  RUN_SPEC = spec;
  RUN_PRESET_ID = PRESET_CACHE[JSON.stringify(spec)] || null;
  PROMPT_RENDERED = r.prompt || "";
  $("#prompt").value = PROMPT_RENDERED;
  summary(2, label); done(2); openStep(3);
}
function genStruct() {
  if (!collectFields().length) { alert("Add at least one field (or pick a unit template)."); openSub(2); return; }
  commitSpec("Custom prompt (structured)");
}
function usePaste() {
  const p = $("#pastebox").value.trim();
  if (!p) { alert("Write or paste your prompt."); return; }
  RUN_SPEC = null; RUN_PRESET_ID = null; PROMPT_RENDERED = "";
  $("#prompt").value = p;
  summary(2, "Custom prompt"); done(2); openStep(3);
}

// ── step 5: upload (dropzone, multi-file) + run ─────────────────────────────
function setStatus(m) { $("#status").textContent = m; }
let FILES = [];

// Logged-out visitors may run FILE_CAP papers (null = no cap). Trim rather than reject so
// dropping a folder still does something useful. Returns false when nothing may run.
function applyFileCap() {
  if (FILE_CAP === null || FILES.length <= FILE_CAP) return true;
  const dropped = FILES.length - FILE_CAP;
  FILES = FILES.slice(0, FILE_CAP);
  setStatus(FILE_CAP === 0
    ? "You've used your free paper. Create a free account to keep extracting, or add your "
      + "own API key under “Use a different model”."
    : `Only ${FILE_CAP} paper without an account — kept the first, skipped ${dropped}. `
      + "Create a free account or add your own API key to run the rest.");
  return FILE_CAP > 0;
}
function setupDropzone() {
  const dz = $("#dropzone"), input = $("#pdf");
  dz.onclick = () => input.click();
  input.onchange = () => { FILES = [...input.files]; applyFileCap(); renderFiles(); };
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => {
    FILES = [...e.dataTransfer.files].filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    applyFileCap();
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

let SUBMITTING = false;    // re-entrancy guard: a 2nd click during the async screen/submit would double-extract
async function run() {
  if (SUBMITTING) return;
  SUBMITTING = true;
  $("#run").disabled = true;                       // disable NOW, not only once inside runBatch
  try {
    const allowed = applyFileCap(); renderFiles();
    if (!allowed) { $("#run").disabled = false; return; }   // status already explains why
    if (!FILES.length) { setStatus("add at least one PDF"); $("#run").disabled = false; return; }
    try { await ensurePreset(); }                    // the design becomes a real preset first
    catch (e) { setStatus("could not save the preset: " + e.message); $("#run").disabled = false; return; }
    const keep = await screenDuplicates();         // resolve any already-extracted papers
    if (keep === null) { setStatus("cancelled"); $("#run").disabled = false; return; }
    if (!keep.length) { setStatus("all selected papers were already extracted — nothing to run"); $("#run").disabled = false; return; }
    STATUS = FILES.map((_, i) => (keep.includes(i) ? { state: "pending" } : { state: "skipped" }));
    renderResults();
    await runBatch(keep, true);                     // navigates away on success; re-enables on retry paths
  } finally { SUBMITTING = false; }
}

// SHA-256 of a File as lowercase hex — matches the server's stored pdf_sha256.
async function sha256Hex(file) {
  const h = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Flag papers whose EXACT PDF was already extracted; let the user skip or re-extract each.
// Returns the FILES indices to actually run (non-duplicates always kept), or null if
// cancelled. A failed check never blocks — it just runs everything.
// Which preset this run is under: a built-in (workflow / summarise), the personal preset
// the designer's spec is saved as (created here, once per design), or none (own prompt).
function runPresetId() {
  if (ADD_DATASET) return null;                                   // the dataset's own schema row
  if (task === "workflow" && presetId) return presetId;
  if (task === "summarise") return "summarize";
  return RUN_PRESET_ID;
}
async function ensurePreset() {
  if (ADD_DATASET || !RUN_SPEC || RUN_PRESET_ID) return runPresetId();
  const key = JSON.stringify(RUN_SPEC);
  if (PRESET_CACHE[key]) return (RUN_PRESET_ID = PRESET_CACHE[key]);
  const r = await api.createPreset({ spec: RUN_SPEC, visibility: "private" });
  PRESET_CACHE[key] = r.id;
  return (RUN_PRESET_ID = r.id);
}
async function screenDuplicates() {
  const all = FILES.map((_, i) => i);
  const schemaId = ADD_DATASET ? (ADD_DATASET.schema_id || schemaIdFor()) : schemaIdFor();
  let dupMap = {};
  try {
    const hashes = await Promise.all(FILES.map(sha256Hex));
    FILES.forEach((f, i) => (f._sha = hashes[i]));
    // a duplicate = same PDF AND same preset (schema_id) → different preset re-extracts freely
    // a duplicate = same PDF AND same preset (any version of it) → a different preset re-extracts freely
    const pid = runPresetId();
    const resp = await api.checkDuplicates([...new Set(hashes)], pid ? null : schemaId, pid);
    dupMap = (resp && resp.duplicates) || {};
  } catch { return all; }
  const dupIdx = all.filter((i) => (dupMap[FILES[i]._sha] || []).length);
  if (!dupIdx.length) return all;
  const decisions = await duplicateModal(dupIdx, dupMap);
  if (decisions === null) return null;              // cancelled
  return all.filter((i) => !(dupMap[FILES[i]._sha] || []).length || decisions[i] === "again");
}

function duplicateModal(dupIdx, dupMap) {
  return new Promise((resolve) => {
    const rows = dupIdx.map((i) => {
      const d = (dupMap[FILES[i]._sha] || [])[0] || {};
      const when = (d.created_at || "").slice(0, 10);
      const recs = d.n_records != null ? `${d.n_records} record${d.n_records === 1 ? "" : "s"}` : "";
      return `<div class="dup-row">
          <div class="dup-name">${esc(FILES[i].name)}</div>
          <div class="dup-meta muted">already extracted${when ? " · " + esc(when) : ""}${recs ? " · " + esc(recs) : ""}</div>
          <div class="dup-choice">
            <label class="radio"><input type="radio" name="dup-${i}" value="skip" checked/> Skip</label>
            <label class="radio"><input type="radio" name="dup-${i}" value="again"/> Extract again</label>
          </div></div>`;
    }).join("");
    const ov = document.createElement("div");
    ov.className = "modal-overlay";
    ov.innerHTML = `<div class="modal" role="dialog" aria-modal="true">
        <h3>${dupIdx.length} paper${dupIdx.length === 1 ? " is" : "s are"} already extracted</h3>
        <p class="muted">Choose what to do with each duplicate (matched by exact PDF). Other papers are extracted normally.</p>
        <div class="dup-list">${rows}</div>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" id="dup-cancel">Cancel</button>
          <button type="button" class="btn btn-primary" id="dup-go">Continue</button>
        </div></div>`;
    document.body.appendChild(ov);
    const collect = () => {
      const o = {};
      dupIdx.forEach((i) => {
        const r = ov.querySelector(`input[name="dup-${i}"]:checked`);
        o[i] = r ? r.value : "skip";
      });
      return o;
    };
    const close = () => ov.remove();
    ov.querySelector("#dup-cancel").onclick = () => { close(); resolve(null); };
    ov.querySelector("#dup-go").onclick = () => { const d = collect(); close(); resolve(d); };
    ov.addEventListener("click", (e) => { if (e.target === ov) { close(); resolve(null); } });
  });
}

async function runBatch(indices, reset) {
  if (!FILES.length) return setStatus("add at least one PDF");
  if (!STATUS.length) STATUS = FILES.map(() => ({ state: "pending" }));
  indices.forEach((i) => (STATUS[i] = { state: "pending" }));   // retried ones go back to pending
  if (reset) $("#result").innerHTML = "";
  $("#run").disabled = true; setStatus(`extracting ${indices.length} paper(s)…`);
  setNav("results"); $("#result").scrollIntoView({ behavior: "smooth" });
  const schemaId = ADD_DATASET ? (ADD_DATASET.schema_id || schemaIdFor()) : schemaIdFor();
  const runStart = new Date().toISOString();
  let anyQueued = false;
  const jobIds = [];                        // queued jobs of this round → tracked in review

  // We forward to the review panel only AFTER every paper is submitted (see end of fn).
  // ?since picks up each paper's document as it lands; ?jobs lets the panel show each
  // still-running paper as "extracting…" and each failure as an error (queued jobs
  // survive navigation on the worker).
  function forwardUrl() {
    const q = jobIds.length ? `&jobs=${encodeURIComponent(jobIds.join(","))}` : "";
    return `/workspace?since=${encodeURIComponent(runStart)}${q}`;
  }

  // Submit EVERY paper before navigating: the browser cancels in-flight uploads on
  // navigation, so forwarding early would drop the un-submitted ones (the bug we just
  // fixed). Fire them ALL at once — the browser caps concurrent connections itself, and a
  // queued job just pushes to the worker — so the review panel opens the moment the batch
  // is submitted, with no second upload round to wait through. It only ENQUEUES (or
  // sync-runs) each paper; the review panel then tracks the running jobs via ?jobs.
  async function submitOne(i) {
    STATUS[i] = { state: "extracting" }; renderResults();
    const fd = new FormData();
    fd.append("pdf", FILES[i]);
    // The server renders the prompt from the preset (+ params) and mints the schema id; an
    // edited prompt is sent as-is (and recorded as edited). Add-papers keeps the dataset's
    // own schema row; an own-written prompt has no preset at all.
    const pid = runPresetId();
    if (pid) fd.append("preset_id", pid);
    if (!pid || promptEdited() || ADD_DATASET) fd.append("prompt", $("#prompt").value);
    if (task === "workflow" && isMasemPreset(presetId) && !ADD_DATASET) fd.append("params", JSON.stringify(readMasemParams()));
    fd.append("schema_id", schemaId);
    if (ADD_DATASET) fd.append("dataset_id", ADD_DATASET.id);   // server attaches the result
    if (USE_CREDITS) {
      fd.append("use_credits", "true");          // server runs on its own key
      // When re-extracting into a dataset, honor that dataset's model — the server keeps it
      // if it's a credit-allowed model, else falls back to the default credit model.
      if (ADD_DATASET && ADD_DATASET.model) fd.append("model", ADD_DATASET.model);
    } else {
      fd.append("model", $("#model").value);
      fd.append("api_key", $("#apikey").value);
    }
    try {
      const data = await api.extract(fd);
      if (data.schema_id) LAST_SCHEMA_ID = data.schema_id;
      if (data.queued) {
        anyQueued = true;
        if (data.job_id) jobIds.push(data.job_id);   // stays "extracting"; the panel tracks it
      } else {
        STATUS[i] = { state: "done", res: data }; renderResults();   // sync completed inline
      }
    } catch (e) { STATUS[i] = { state: "failed", error: e.message }; renderResults(); }
  }
  await Promise.all(indices.map(submitOne));

  $("#run").disabled = false; setStatus("done");
  renderResults(true);
  // Forward ONCE, after every paper has been submitted: queued jobs survive navigation and
  // the review panel tracks them via ?jobs; any sync results are already in hand.
  if (reset && (anyQueued || STATUS.some((s) => s.state === "done"))) {
    if (ADD_DATASET) {
      // Add-papers: the server attaches each finished paper to the dataset, so jump to the
      // dataset's Data review and watch them fill in (?jobs tracks the queued ones).
      const jq = jobIds.length ? `&jobs=${jobIds.join(",")}` : "";
      location.href = `/workspace?project=${encodeURIComponent(ADD_DATASET.id)}${jq}`;
    } else {
      location.href = forwardUrl();
    }
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
    else if (s.state === "skipped") right = `<span class="muted">— skipped (already extracted)</span>`;
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
    const recipe = { prompt: $("#prompt").value, model: $("#model").value, schema_id: LAST_SCHEMA_ID || schemaIdFor() };
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
