// Analysis/dashboard builder — Entry B (over an existing dataset): pick a dataset,
// state a goal, let an LLM propose figures, edit them with a live D3 preview, save
// as a dashboard Analysis (a saved_view). Vanilla ES module; keys stay in-browser.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { getKey, setKey } from "/static/keys.js";
import { renderDashboard } from "/static/figures.js";
import { createTrace } from "/static/trace.js";
import { renderComingSoon } from "/static/coming-soon.js";

const $ = (s) => document.querySelector(s);
const CHART_KINDS = ["bar", "grouped_bar", "stacked_bar", "line", "scatter", "histogram", "forest"];
const AGGS = ["count", "mean", "median", "sum"];
const PAPER_VARS = ["year", "primary_topic"];

let MODELS = {}, FIGURES = [], ROWS = [], VARS = [], DSID = null, LAST_RAW = "";

// The analysis/dashboard builder is being rebuilt and is disabled in the beta.
// init() short-circuits to a "coming soon" placeholder; the machinery below is
// kept intact for a later re-enable (see WS8 in the beta plan).
async function init() {
  renderComingSoon(document.querySelector("main"), {
    title: "Dashboards are coming soon",
    note: "The analysis & dashboard builder is being rebuilt and isn’t available in the beta yet. "
      + "You can still extract papers and review the extracted data.",
  });
}
// eslint-disable-next-line no-unused-vars
async function _initBuilder() {
  await loadModels();
  await loadDatasets();
  $("#provider").onchange = fillModels;
  $("#apikey").oninput = () => setKey($("#provider").value, $("#apikey").value.trim());
  $("#propose").onclick = propose;
  $("#addfig").onclick = () => { FIGURES.push(blankFigure()); renderEditor(); preview(); };
  $("#vibe").onchange = () => { applyVibe(); preview(); };
  $("#viewraw").onclick = toggleRaw;
  $("#save").onclick = save;
  // chooser: existing dataset (works) vs from-scratch papers flow (coming soon)
  $("#ch-existing").onclick = showWizard;
  $("#ch-scratch").onclick = () => { $("#ch-note").textContent =
    "The from-scratch flow — upload example papers, let AI propose figures and extract the data — is coming soon. For now, extract papers first, then build a dashboard over the dataset."; };
  if (new URLSearchParams(location.search).get("dataset")) showWizard();
  else $("#chooser").hidden = false;
}
function showWizard() { $("#chooser").hidden = true; $("#wizard").hidden = false; }

// ── models + keys (mirrors extract.js) ───────────────────────────────────────
async function loadModels() {
  try { MODELS = (await api.models()).providers || {}; } catch { MODELS = {}; }
  $("#provider").innerHTML = Object.keys(MODELS).map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
  fillModels();
}
function fillModels() {
  const p = $("#provider").value;
  $("#model").innerHTML = (MODELS[p] || []).map((m) => `<option value="${esc(m.value)}">${esc(m.label)}</option>`).join("");
  $("#keylabel").textContent = `${p} API key`;
  $("#apikey").value = getKey(p);
}

async function loadDatasets() {
  let datasets = [], me = null;
  try { [me, datasets] = await Promise.all([api.me(), api.myDatasets().then((r) => r.datasets || [])]); } catch { /* */ }
  const want = new URLSearchParams(location.search).get("dataset");
  const owned = (d) => me && me.id && d.owner_user_id === me.id;
  const opt = (d) => `<option value="${esc(d.id)}"${d.id === want ? " selected" : ""}>${esc(d.title || "untitled")} · ${d.n_records} rec</option>`;
  const mine = datasets.filter(owned), pub = datasets.filter((d) => !owned(d));
  let html = "";
  if (mine.length) html += `<optgroup label="Your datasets">${mine.map(opt).join("")}</optgroup>`;
  if (pub.length) html += `<optgroup label="Public datasets">${pub.map(opt).join("")}</optgroup>`;
  $("#dspick").innerHTML = html || '<option value="">(no datasets — extract some papers first)</option>';
}

// ── propose ──────────────────────────────────────────────────────────────────
async function propose() {
  DSID = $("#dspick").value;
  if (!DSID) return setStatus("pick a dataset first");
  const api_key = $("#apikey").value.trim();
  if (!api_key) return setStatus("enter your API key");
  const btn = $("#propose"); btn.disabled = true; setStatus("loading data + asking the model…");
  try {
    const rowsResp = await api.datasetRows([DSID]);
    ROWS = (rowsResp && rowsResp.rows) || [];
    VARS = [...new Set(ROWS.flatMap((r) => Object.keys(r.field_values || {}))), ...PAPER_VARS];
    const res = await api.proposeFigures({
      entry: "dataset", dataset_id: DSID, goals: $("#goal").value.trim(),
      model: $("#model").value, api_key, base_url: null,
    });
    if (!res.ok) throw new Error(res.error || "proposal failed");
    LAST_RAW = res.raw || "";
    $("#viewraw").hidden = !LAST_RAW;
    const figs = res.figures || [];
    FIGURES = figs.length ? figs : [blankFigure()];
    let msg = figs.length
      ? `proposed ${figs.length} figure${figs.length === 1 ? "" : "s"}`
      : "the model didn’t return usable figures — starting from a blank one you can configure";
    if ((res.dropped || []).length) msg += ` · ${res.dropped.length} skipped`;
    setStatus(msg);
    if (!$("#aname").value) $("#aname").value = $("#goal").value.trim().slice(0, 60) || "My analysis";
    $("#build").hidden = false;
    renderEditor(); preview();
  } catch (e) { setStatus("✗ " + e.message); }
  finally { btn.disabled = false; }
}

// ── figure editor ────────────────────────────────────────────────────────────
function blankFigure() {
  return { id: "figure", title: "New figure", question: "", chart_kind: "bar",
    encodings: { x: null, y: null, color: null, error: null },
    transform: { aggregate: "count", group_by: [] }, scales: {},
    required_variables: [], data_sufficiency: "ok" };
}
const varOptions = (sel) => `<option value="">(none)</option>`
  + VARS.map((v) => `<option value="${esc(v)}"${v === sel ? " selected" : ""}>${esc(v)}</option>`).join("");

function renderEditor() {
  const wrap = $("#figcards");
  wrap.innerHTML = FIGURES.map((f, i) => {
    const enc = f.encodings || {};
    const gv = (ch) => (enc[ch] && enc[ch].var) || "";
    const isForest = f.chart_kind === "forest";
    return `<div class="fig-card" data-i="${i}">
      <div class="fc-top">
        <input class="fc-title" data-k="title" value="${esc(f.title || "")}" placeholder="figure title"/>
        <button class="fc-del" title="remove figure">🗑</button>
      </div>
      <div class="fc-grid">
        <label>Chart<select data-k="chart_kind">${CHART_KINDS.map((k) => `<option value="${k}"${k === f.chart_kind ? " selected" : ""}>${k}</option>`).join("")}</select></label>
        <label>X / value<select data-enc="x">${varOptions(gv("x"))}</select></label>
        <label>Y / category<select data-enc="y">${varOptions(gv("y"))}</select></label>
        <label>Color<select data-enc="color">${varOptions(gv("color"))}</select></label>
        <label>Aggregate<select data-k="aggregate">${AGGS.map((a) => `<option value="${a}"${a === (f.transform && f.transform.aggregate) ? " selected" : ""}>${a}</option>`).join("")}</select></label>
        ${isForest ? `<label>CI low<select data-err="lo">${varOptions(enc.error && enc.error.lo)}</select></label>
        <label>CI high<select data-err="hi">${varOptions(enc.error && enc.error.hi)}</select></label>` : ""}
      </div>
      ${f.data_sufficiency === "insufficient" ? `<div class="fc-warn">⚠ needs data not clearly present in this dataset</div>` : ""}
    </div>`;
  }).join("") || '<p class="muted">No figures. Click “Add figure”.</p>';

  wrap.querySelectorAll(".fig-card").forEach((card) => {
    const i = +card.dataset.i, f = FIGURES[i];
    card.querySelector(".fc-del").onclick = () => { FIGURES.splice(i, 1); renderEditor(); preview(); };
    card.querySelector('[data-k="title"]').onchange = (e) => { f.title = e.target.value; preview(); };
    card.querySelector('[data-k="chart_kind"]').onchange = (e) => {
      f.chart_kind = e.target.value;
      if (f.chart_kind === "forest") f.encodings.error = f.encodings.error || { lo: "", hi: "" };
      renderEditor(); preview();
    };
    card.querySelector('[data-k="aggregate"]').onchange = (e) => {
      f.transform = f.transform || {}; f.transform.aggregate = e.target.value; preview();
    };
    card.querySelectorAll("[data-enc]").forEach((sel) => (sel.onchange = (e) => {
      const ch = sel.dataset.enc;
      f.encodings[ch] = e.target.value ? { var: e.target.value } : null;
      preview();
    }));
    card.querySelectorAll("[data-err]").forEach((sel) => (sel.onchange = (e) => {
      f.encodings.error = f.encodings.error || {};
      f.encodings.error[sel.dataset.err] = e.target.value;
      preview();
    }));
  });
}

function preview() {
  let panel = document.querySelector("#trace-panel");
  if (!panel) { panel = document.createElement("div"); panel.id = "trace-panel"; panel.className = "trace-panel"; panel.hidden = true; $("#preview").after(panel); }
  const trace = createTrace(ROWS, panel, { navigate: false });   // don't leave an unsaved builder
  renderDashboard($("#preview"), { figures: FIGURES }, ROWS, trace.opts);
}

// show the model's raw figure-suggestion JSON (pretty-printed if parseable)
function toggleRaw() {
  const box = $("#rawbox");
  if (!box.hidden) { box.hidden = true; return; }
  let pretty = LAST_RAW;
  try { pretty = JSON.stringify(JSON.parse(LAST_RAW), null, 2); } catch { /* show as-is */ }
  box.textContent = pretty;
  box.hidden = false;
}
function applyVibe() {
  const v = $("#vibe").value;
  if (v) document.body.setAttribute("data-vibe", v); else document.body.removeAttribute("data-vibe");
}

// ── save ─────────────────────────────────────────────────────────────────────
async function save() {
  if (!DSID || !FIGURES.length) return sset("nothing to save");
  const title = $("#aname").value.trim() || "Untitled analysis";
  const visibility = (document.querySelector("input[name='avis']:checked") || {}).value || "private";
  const vibe = $("#vibe").value || null;
  const btn = $("#save"); btn.disabled = true; sset("saving…");
  try {
    const view = await api.createView({
      title, view_type: "dashboard", dataset_ids: [DSID], visibility,
      viz_config: {
        goals: $("#goal").value.trim(), figures: FIGURES, theme: { vibe },
        proposal: { raw: LAST_RAW, model: $("#model").value },
      },
    });
    location.href = `/analysis?view=${view.id}`;
  } catch (e) { sset("✗ " + e.message); btn.disabled = false; }
}

const setStatus = (m) => ($("#pstatus").textContent = m);
const sset = (m) => ($("#sstatus").textContent = m);

init();
