// Workspace — PDF pages + highlight overlays alongside the extracted records,
// with click-to-source, verify/flag, and edit-in-place (corrections route to
// the verification layer). Uses the shared grammar + pdfview modules.
//
// The review layout is DECLARED by the preset (DATA.spec, via spec.js): paper-level fields,
// entries, sub-entries, tabs, and which confidence group each field belongs to. Documents
// extracted before the declarative format arrive with a spec the server inferred from
// their old grammar + data (VM.legacy) and keep the data-driven behaviours that existed
// only because presets could not declare them (constant hoisting, shape-driven layout).
import { api } from "/static/api.js";
import { addPapersMenu } from "/static/addpapers.js";
import { renderValue, renderFields, renderChild, renderConfBadge, renderConfDot, renderMarkdown, esc, formatKey, stripRowIds, freeChip } from "/static/grammar.js";
import { renderPages, jumpToEvidence, showEvidence, hideEvidence, flashRects, setContextEvidence } from "/static/pdfview.js";
import { saveToWorkspace } from "/static/save.js";
import { renderGrid } from "/static/gridview.js";
import { viewModel, indexEvidence, evidenceFor, entryTitle, childTitle, worstLevel, isLow, entryNeeds,
         undeclared, seedEntry, orderedEntries, gridColumns, gridRows, relPath, parseRel } from "/static/spec.js";

const $ = (s, el = document) => el.querySelector(s);
let DATA = null, DOCS = [], DOCID = null, RAW = false, GRID = false, PROJECT = null, PROJECT_TITLE = "", PROJECT_MODE = null, FOCUS_REC = null;
let CHAIN_OFF = false;                                   // a preset with a review layout (chain, evidence, stages), but the reviewer wants the field view
let STAGE = 0;                                           // the staged layout: which stage is open (kept across papers)
let STAGE_CUR = 0, STAGE_KEY = "", STAGE_ITEMS = [], STAGE_SHOWN = "", STAGE_GO = 0;   // its cursor over the stage's items, per paper and stage
const CHAIN_EDIT = new Set();                            // single claims flipped to the editable field card
let AUX = { dataset: null, cross: null, concepts: null };   // per dataset: cross-check verdicts, concept ids (chain layout)
let PANEL_SEL = null;   // multi-entry panel nav: null(default→"paper") | "paper" | record index
let JOBS = {};   // job_id -> {status:'pending'|'complete'|'failed', document_id?, error?} — this-round tracking
let JOBS_POLLED = false;   // suppress "extracting…" placeholders until we've checked real status once
let ACCOUNT = false;  // logged in? (exports need no account; kept for owner-only actions)
let VM = null, EV = null;   // the preset's view model + the evidence index for the loaded document
let TRIAGE = "order";       // entry nav order: order | low_conf | unverified | flagged
let AUTOADV = true;         // after ✓ / ⚑ jump to the next unreviewed entry
try { TRIAGE = localStorage.getItem("metalens_triage") || TRIAGE; AUTOADV = localStorage.getItem("metalens_autoadvance") !== "0"; } catch { /* */ }

// Drag the splitter to resize the entries panel; width persists across sessions — one width
// for the generic view, one for a stages preset, whose default gives the PDF column two fifths
// of the window and the stage panel the rest.
function panelWidthKey() { return stagesOpen() ? "metalens_panel_w_stages" : "metalens_panel_w"; }
let PANEL_MODE = null;
function applyPanelWidth() {
  const panel = $("#panel"); if (!panel) return;
  const mode = panelWidthKey();
  if (mode === PANEL_MODE) return;
  PANEL_MODE = mode;
  const saved = parseInt(localStorage.getItem(mode) || "", 10);
  if (saved) { panel.style.width = saved + "px"; return; }
  if (!stagesOpen()) { panel.style.width = ""; return; }           // the stylesheet's default
  const main = panel.parentElement, side = $("#doctabs");
  const pdf = Math.round(window.innerWidth * 0.4);
  const w = main.getBoundingClientRect().width - (side && side.offsetParent ? side.getBoundingClientRect().width : 0) - 6 - pdf;
  panel.style.width = Math.max(300, Math.round(w)) + "px";
}
function mountSplitter() {
  const sp = $("#splitter"), panel = $("#panel");
  if (!sp || !panel) return;
  applyPanelWidth();
  let dragging = false;
  sp.addEventListener("mousedown", (e) => {
    dragging = true; sp.classList.add("drag"); document.body.style.userSelect = "none"; e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!dragging) return;
    // as wide as the window allows, keeping a grabbable strip of the PDF pane
    const maxW = window.innerWidth - panel.getBoundingClientRect().left - 140;
    const w = Math.max(300, Math.min(maxW, e.clientX - panel.getBoundingClientRect().left));
    panel.style.width = w + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false; sp.classList.remove("drag"); document.body.style.userSelect = "";
    localStorage.setItem(panelWidthKey(), String(parseInt(panel.style.width, 10) || 480));
  });
}

// Zoom the PDF pages: − / + / fit buttons and Ctrl/⌘ + wheel over the pages. A factor
// above 1 makes a page wider than the pane (it scrolls sideways) — the way to read a page
// when the review panel takes most of the window. Persists across sessions.
function paneZoom(z) {                                   // the pages' scale, not remembered
  const pages = $("#pages"), val = $("#pdfzoomval");
  if (pages) pages.style.setProperty("--pdf-zoom", String(z));
  if (val) val.textContent = z === 1 ? "fit" : `${Math.round(z * 100)}%`;
}
function storedZoom() { return parseFloat(localStorage.getItem("metalens_pdf_zoom") || "1") || 1; }
function mountPdfZoom() {
  const pages = $("#pages"), box = $("#pdfzoom");
  if (!pages || !box) return;
  let z = storedZoom();
  const apply = () => {
    z = Math.min(4, Math.max(0.5, Math.round(z * 100) / 100));
    paneZoom(z);
    localStorage.setItem("metalens_pdf_zoom", String(z));
  };
  box.querySelectorAll("button[data-z]").forEach((b) => (b.onclick = () => {
    z = b.dataset.z === "in" ? z * 1.25 : b.dataset.z === "out" ? z / 1.25 : 1; apply();
  }));
  pages.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault(); z = e.deltaY < 0 ? z * 1.1 : z / 1.1; apply();
  }, { passive: false });
  apply();
}

async function init() {
  mountSplitter();
  mountPdfZoom();
  mountKeys();
  try { ACCOUNT = !!(await api.me()); } catch { ACCOUNT = false; }
  const q = new URLSearchParams(location.search);
  PROJECT = q.get("project") || null;
  const docsParam = q.get("docs");    // comma list → scope to one extraction round
  const sinceParam = q.get("since");  // ISO time → docs from this extraction round (live)
  const jobsParam = q.get("jobs");    // comma list of this round's job ids → track pending/errors
  FOCUS_REC = q.get("rec") || null;   // focus + flash a specific record (from a chart)
  let wantDoc = q.get("doc");
  // ?rec without ?doc (a chart deep-link) → resolve the record's document first
  if (FOCUS_REC && !wantDoc && !PROJECT && !docsParam && !sinceParam) {
    try { const rd = await api.record(FOCUS_REC); wantDoc = rd.record && rd.record.document_id; } catch { /* */ }
  }
  // Data-review landing (no dataset, round, or specific doc): fall through to load
  // ALL of the user's documents — the most recent (last run) is auto-selected below,
  // and if there are none the empty-state shows a "Turn a paper into data" button.
  try {
    if (PROJECT) {
      DOCS = (await api.documents({ dataset: PROJECT })).documents || [];
      try { const ov = await api.datasetOverview(PROJECT); PROJECT_TITLE = ov.title || ""; PROJECT_MODE = (ov.recipe || {}).mode || null; } catch { /* */ }
    } else if (docsParam) {
      const want = new Set(docsParam.split(",").filter(Boolean));
      DOCS = ((await api.documents({})).documents || []).filter((d) => want.has(d.document_id));
      PROJECT_TITLE = "This extraction";
    } else if (sinceParam) {
      DOCS = ((await api.documents({})).documents || [])
        .filter((d) => d.created_at && new Date(d.created_at) >= new Date(sinceParam));
      PROJECT_TITLE = "This extraction";
    } else {
      DOCS = (await api.documents({})).documents || [];
    }
  } catch (e) { $("#pages").innerHTML = `<p class="muted">error: ${esc(e.message)}</p>`; return; }
  // track this round's jobs (shows each still-running paper + any failures); ?jobs comes
  // with ?since, so we reload the round's documents as jobs complete.
  if (jobsParam) startJobTracking(jobsParam.split(",").filter(Boolean), sinceParam);
  else if (sinceParam) startRoundPoll(sinceParam);
  if (!DOCS.length) {
    renderDocTabs();
    $("#pages").innerHTML = `<div class="empty-state">
        <h3>${PROJECT ? "This dataset has no papers yet" : "No extractions yet"}</h3>
        <p class="muted">Turn a paper into structured data — pull out the values you care about, then verify each one against its highlighted source.</p>
        <a class="btn btn-primary" href="/extract">Turn a paper into data →</a>
      </div>`;
    $("#panelbody").innerHTML = "";
    return;
  }
  DOCID = (wantDoc && DOCS.some((d) => d.document_id === wantDoc)) ? wantDoc : DOCS[0].document_id;
  renderDocTabs();
  load(DOCID);
}

function selectDoc(docId) { DOCID = docId; renderDocTabs(); load(docId); }

// While a parallel extraction round finishes, poll for siblings still processing and
// slot them into the sidebar as they land. Stops once things go quiet (~40s idle).
function startRoundPoll(since) {
  let idle = 0;
  const timer = setInterval(async () => {
    let docs = [];
    try {
      docs = ((await api.documents({})).documents || [])
        .filter((d) => d.created_at && new Date(d.created_at) >= new Date(since));
    } catch { return; }
    if (docs.length > DOCS.length) { DOCS = docs; renderDocTabs(); idle = 0; }
    else if (++idle >= 5) clearInterval(timer);    // ~40s with no new papers
  }, 8000);                                        // 8s poll (was 4s) — fewer document-list queries
}

async function reloadRoundDocs(since) {
  try {
    // In a dataset view (add-papers), the server attaches each finished paper to the
    // dataset, so a dataset-scoped refetch naturally grows to include the new ones.
    if (PROJECT) { DOCS = (await api.documents({ dataset: PROJECT })).documents || []; return; }
    const all = (await api.documents({})).documents || [];
    // a completed job's document belongs to this round even if a clock skew would push
    // its created_at just under `since` — include it by id as well as by the time filter.
    const jobDocs = new Set(Object.values(JOBS)
      .filter((j) => j.status === "complete" && j.document_id).map((j) => j.document_id));
    DOCS = all.filter((d) => jobDocs.has(d.document_id)
      || !since || (d.created_at && new Date(d.created_at) >= new Date(since)));
  } catch { /* keep current */ }
}

// Track this round's jobs: show each still-running paper as "extracting…", each failure
// as an error, and pull in each document as its job completes. Survives navigation
// because the jobs run on the worker, not the page.
//
// CRUCIAL for refresh: ?jobs stays in the URL, so a reload re-tracks the SAME ids. If we
// blindly stamped them "pending" and rendered, jobs that already FINISHED before the
// reload would flash as "extracting…" beside their real doc tabs — looking like a re-run.
// So the first status check runs immediately (in parallel), and placeholders stay hidden
// until it classifies each job; finished ones become real tabs, only truly-running ones
// show a spinner. Once nothing is pending, ?jobs is dropped from the URL so later reloads
// are clean.
function startJobTracking(jobIds, since) {
  jobIds.forEach((id) => (JOBS[id] = { status: "pending" }));
  renderDocTabs();                                     // DOCS now; placeholders hidden (JOBS_POLLED=false)
  let ticks = 0;
  const tick = async () => {
    const seen = await Promise.all(jobIds.map(async (id) => {
      const j = JOBS[id];
      if (!j || j.status === "complete" || j.status === "failed") return "settled";
      try {
        const st = await api.job(id);
        if (st.success === true) { j.status = "complete"; j.document_id = st.result && st.result.document_id; return "new"; }
        if (st.success === false) { j.status = "failed"; j.error = st.error || "extraction failed"; return "settled"; }
      } catch { /* transient — treat as still pending */ }
      return "pending";
    }));
    JOBS_POLLED = true;
    if (seen.includes("new")) await reloadRoundDocs(since);
    renderDocTabs();
    // Nothing extracted yet and a paper failed → surface the reason instead of a blank panel.
    if (!DOCID && !DOCS.length) {
      const failed = Object.entries(JOBS).find(([, j]) => j.status === "failed");
      if (failed) showJobError(failed[0]);
    }
    return seen.includes("pending");
  };
  const loop = (pending) => {
    if (!pending) {                                    // all settled → drop ?jobs so a reload is clean
      const u = new URL(location.href); u.searchParams.delete("jobs");
      history.replaceState(null, "", u);
      return;
    }
    const timer = setInterval(async () => {
      if (!(await tick()) || ++ticks > 45) {           // done, or ~6 min safety cap
        clearInterval(timer);
        const u = new URL(location.href); u.searchParams.delete("jobs"); history.replaceState(null, "", u);
      }
    }, 8000);                                          // 8s poll — halves Upstash job-status polling
  };
  tick().then(loop);                                   // first check immediately, not after 8s
}

// While papers of this round are still extracting, "Save" would bundle an incomplete
// set: show a waiting indicator with the progress in its place until every job has settled.
function syncJobWait() {
  const wait = $("#jobwait"), save = $("#dlsave"); if (!wait || !save) return;
  const jobs = Object.values(JOBS);
  const pending = jobs.filter((j) => j.status === "pending").length;
  wait.hidden = !pending; save.hidden = !!pending;
  if (pending) $("#jobwait-txt").textContent = `Extracting… ${jobs.length - pending} of ${jobs.length} paper${jobs.length === 1 ? "" : "s"} done`;
}

// paper switcher — a tab per document, above the records
function renderDocTabs() {
  const strip = $("#doctabs"); if (!strip) return;
  // the dataset these papers belong to — a link, so an import (which always lands in a
  // dataset) is one click from its dataset page
  const label = !PROJECT_TITLE ? ""
    : PROJECT ? `<a class="doctabs-label" href="/dataset?id=${esc(PROJECT)}" title="open this dataset">${esc(PROJECT_TITLE)} ↗</a>`
    : `<span class="doctabs-label">${esc(PROJECT_TITLE)}</span>`;
  // Add-papers entry point: from a dataset's review, jump back to the extract page in
  // add-papers mode (drag-drop first, this dataset's recipe reused) to add more.
  const importOnly = PROJECT_MODE === "import" || !!(DATA && DATA.spec && DATA.spec.meta && DATA.spec.meta.mode === "import");
  const addBtn = PROJECT ? addPapersMenu(PROJECT, importOnly, "doctab doctab-add") : "";
  const docTabs = DOCS.map((d) => {
    const nm = d.filename || d.title || "untitled";
    const shade = docShade(d);
    return `<button class="doctab${d.document_id === DOCID ? " active" : ""}${shade ? " " + shade : ""}" data-id="${d.document_id}" title="${esc(nm)}${shade === "reviewed" ? " · reviewed" : shade === "flagged" ? " · flagged" : ""}">`
      + `${esc(nm.slice(0, 30))}<span class="dt-n">${d.n_records}</span></button>`
      + (d.document_id === DOCID ? docSubList() : "");     // the open paper's parts, one line each
  }).join("");
  // still-running or failed papers from this round that aren't yet a visible document.
  // Hidden until the first status check (JOBS_POLLED) so a refresh of already-finished
  // jobs doesn't flash "extracting…"; and never a placeholder for a doc already shown.
  const shown = new Set(DOCS.map((d) => d.document_id));
  const jobTabs = (JOBS_POLLED ? Object.entries(JOBS) : []).filter(([, j]) =>
    !(j.document_id && shown.has(j.document_id))
    && (j.status === "pending" || j.status === "failed" || (j.status === "complete" && j.document_id))
  ).map(([id, j]) => j.status === "failed"
    ? `<button class="doctab jobfail" data-jobid="${esc(id)}" title="click to see why">✗ failed</button>`
    : `<span class="doctab jobpend">⏳ extracting…`
      + `<button class="jobstop" data-jobid="${esc(id)}" title="stop this extraction">✕</button></span>`).join("");
  strip.innerHTML = label + docTabs + jobTabs + addBtn;
  syncJobWait();
  strip.querySelectorAll(".doctab[data-id]").forEach((b) => (b.onclick = () => selectDoc(b.dataset.id)));
  strip.querySelectorAll(".docsub-item").forEach((b) => (b.onclick = () => selectEntry(b.dataset.sel === "paper" ? "paper" : +b.dataset.sel)));
  strip.querySelectorAll(".jobfail[data-jobid]").forEach((b) => (b.onclick = () => showJobError(b.dataset.jobid)));
  strip.querySelectorAll(".jobstop[data-jobid]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); cancelJob(b.dataset.jobid, b); }));
}

// The sidebar's colour for a paper: green once every entry is verified, orange while any is flagged.
function docShade(d) {
  if ((d.n_flagged || 0) > 0) return "flagged";
  return d.n_records > 0 && (d.n_verified || 0) >= d.n_records ? "reviewed" : "";
}

// Under the open paper in the sidebar: "Paper" + one line per entry (an experiment, a
// sample …) in the panel's triage order, each with its review status. Clicking one shows
// that part in the panel — the same navigation as the tabs above the cards.
function docSubList() {
  if (!DATA || !VM || !Array.isArray(DATA.records) || DATA.document_id !== DOCID || !DATA.records.length) return "";
  const ordered = orderedEntries(VM, EV, DATA.records, TRIAGE, DATA.issues);
  const many = DATA.records.length > 1;
  const cur = !many ? null : PANEL_SEL === null ? (TRIAGE === "order" ? "paper" : ordered[0].i) : PANEL_SEL;
  const item = (sel, label, status) =>
    `<button class="docsub-item${cur === sel ? " active" : ""}" data-sel="${sel}" title="${esc(label)}${status ? " · " + esc(status) : ""}">`
    + `<span class="docsub-title">${esc(shortTitle(label))}</span>${status ? `<span class="st-dot ${esc(status)}"></span>` : ""}</button>`;
  return `<div class="docsub">` + (paperPanelHasContent() ? item("paper", "📄 Paper", "") : "")
    + ordered.map(({ rec, i }) => item(i, entryTitle(VM, rec, i), rec.verification_status)).join("") + `</div>`;
}

// "Experiment PRISMA · Appraise systematic reviews …" → "Experiment PRISMA" for the narrow sidebar
function shortTitle(title) {
  const head = String(title || "").split(" · ")[0].trim();
  return head || title;
}

// Stop a queued/running extraction and drop its tab.
async function cancelJob(jobId, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "…"; }
  try { await api.cancelJob(jobId); } catch { /* best-effort — the poll will settle it anyway */ }
  delete JOBS[jobId];
  renderDocTabs();
}

// Show a failed job's cleaned error in the main area (there's no document to open for it),
// with a one-click Retry that re-runs the same paper from its stored args (no re-upload).
function showJobError(jobId) {
  const job = JOBS[jobId]; if (!job) return;
  DOCID = null; renderDocTabs();
  $("#panelbody").innerHTML = "";
  $("#pages").innerHTML = `<div class="empty-state">`
    + `<h3>Extraction failed</h3>`
    + `<p class="muted" style="white-space:pre-wrap">${esc(job.error || "The extraction failed.")}</p>`
    + `<div class="empty-actions" style="justify-content:center">`
    + `<button class="btn btn-primary" id="job-retry">🔁 Retry</button>`
    + `<a class="btn btn-ghost" href="/extract${PROJECT ? `?dataset=${esc(PROJECT)}` : ""}">Re-upload instead</a>`
    + `</div><p class="muted" style="margin-top:10px">Often an API key, model, or quota issue.</p></div>`;
  const rb = $("#job-retry"); if (rb) rb.onclick = () => retryJob(jobId, rb);
}

// Re-run a failed extraction from its original args; track the fresh attempt in place.
async function retryJob(jobId, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "Retrying…"; }
  try {
    const r = await api.retryJob(jobId);
    delete JOBS[jobId];                                 // replace the failed attempt
    $("#pages").innerHTML = `<div class="empty-state"><h3>Retrying…</h3>`
      + `<p class="muted">Re-running the extraction — watch the tab above.</p></div>`;
    startJobTracking([r.job_id], null);                 // seed + poll the new job
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = "🔁 Retry"; }
    alert("Couldn't retry: " + e.message);
  }
}

async function load(docId) {
  DOCID = docId;
  PANEL_SEL = null;                 // reset the entry nav when switching papers
  $("#pages").innerHTML = '<p class="muted">Loading…</p>';
  await pendingSaves();             // an edit made a moment ago is on the server before we re-read
  if (DOCID !== docId) return;      // the coder moved on again meanwhile
  DATA = await api.documentView(docId);
  // "Screened — no records" sentinels aren't data rows: drop them so the review shows the
  // screened empty-state (not an empty card), but keep the sentinel so the reviewer can
  // CONFIRM "no records" (verify it) — remember whether the doc was screened.
  DATA._sentinel = (DATA.records || []).find((r) => r.screened_empty) || null;
  DATA._screened = !!DATA._sentinel;
  DATA.records = (DATA.records || []).filter((r) => !r.screened_empty);
  VM = viewModel(DATA);
  EV = indexEvidence(DATA, VM);
  if (!localStorage.getItem("metalens_triage") && VM.triage === "low_confidence_first") TRIAGE = "low_conf";
  renderPages($("#pages"), DATA.pages, DATA.evidence);
  renderPanel();
  if (FOCUS_REC) { focusRecord(FOCUS_REC); FOCUS_REC = null; }   // one-shot deep-link focus
  if (chainSpec()) loadChainAux(docId);
}

// Scroll to a specific record's card, pulse it, and flash its first evidence (from a
// chart deep-link, /workspace?doc=&rec=).
function focusRecord(rec) {
  const idx = (DATA.records || []).findIndex((x) => x.id === rec);
  if (idx >= 0 && DATA.records.length > 1) { PANEL_SEL = idx; renderPanel(); }
  const card = document.querySelector(`.record[data-rid="${rec}"]`);
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("focus");
    setTimeout(() => card.classList.remove("focus"), 2400);
  }
  const r = (DATA.records || []).find((x) => x.id === rec);
  if (r) {
    const evs = recordEvidence(r);
    if (evs.length) setTimeout(() => jumpToEvidence(evs[0].ev.page, evs[0].i), 500);
  }
}

function recordEvidence(rec) {
  return DATA.evidence
    .map((ev, i) => ({ ev, i }))
    .filter(({ ev }) => ev.record_id === rec.id || (ev.record_id == null && ev.entry_index === rec.entry_index));
}

// Evidence the model never tied to a specific record (no record_id, no entry_index —
// e.g. a masem `field:"records"` with no samples[i] prefix). Listed under "Uncited
// sources"; never attached to cells (it used to make every cell look sourced).
function orphanEvidence() {
  return (EV ? EV.orphans : []).map((i) => ({ ev: DATA.evidence[i], i }));
}

// ── legacy documents: the data-driven layout that predates declared presets ─────────
// Fields whose value is IDENTICAL across every record → shown once in the paper panel
// (with >1 record only; a single record keeps everything in the entry). Recomputed per
// document load. CONSTANT maps key → shared value; entry views drop these keys.
let CONSTANT = {};
function computeConstant() {
  CONSTANT = {};
  if (!VM || !VM.legacy) return;
  const recs = DATA.records || [];
  if (recs.length <= 1) return;
  const skip = new Set(["evidence", "extraction_confidence", "confidence"]);
  const keys = new Set();
  recs.forEach((r) => Object.keys(r.field_values || {}).forEach((k) => { if (!skip.has(k)) keys.add(k); }));
  for (const k of keys) {
    const first = JSON.stringify((recs[0].field_values || {})[k]);
    if (recs.every((r) => JSON.stringify((r.field_values || {})[k]) === first)) {
      const v = (recs[0].field_values || {})[k];
      if (v !== null && v !== undefined && v !== "") CONSTANT[k] = v;
    }
  }
}
const _isConstant = (k) => Object.prototype.hasOwnProperty.call(CONSTANT, k);

function _subViews() {
  const sv = DATA.field_defs && DATA.field_defs.sub_views;
  return Array.isArray(sv) && sv.length > 1 ? sv : null;
}
function entryFields(fv) {
  const out = {};
  for (const [k, v] of Object.entries(fv || {})) {
    if (k === "evidence" || k === "extraction_confidence" || k === "confidence" || _isConstant(k)) continue;
    out[k] = v;
  }
  return out;
}
function _fieldsForView(fv, view) {
  const skip = new Set(["evidence", "extraction_confidence", "confidence"]);
  const inc = Array.isArray(view.include_keys) && view.include_keys.length ? new Set(view.include_keys) : null;
  const exc = new Set(view.exclude_keys || []);
  const out = {};
  for (const [k, v] of Object.entries(fv || {})) {
    if (skip.has(k) || _isConstant(k)) continue;   // constant → paper panel, not the entry
    if (inc ? inc.has(k) : !exc.has(k)) out[k] = v;
  }
  return out;
}
function fieldTypes() { return (DATA.field_defs && DATA.field_defs.field_types) || {}; }
function renderHints() { return (DATA.field_defs && DATA.field_defs.render_hints) || {}; }

function renderLegacyBody(rec) {
  const eopts = { editable: true, fieldTypes: fieldTypes(), renderHints: renderHints() };
  const views = _subViews();
  if (!views) return renderValue(entryFields(rec.field_values), eopts);
  const present = views.filter((v) => Object.keys(_fieldsForView(rec.field_values, v)).length);
  if (present.length < 2) return renderValue(entryFields(rec.field_values), eopts);
  const tabs = present.map((v, i) =>
    `<button class="subtab${i === 0 ? " active" : ""}" data-vi="${i}">${esc(v.label || v.id)}</button>`).join("");
  const panels = present.map((v, i) =>
    `<div class="subpanel${i === 0 ? "" : " hidden"}" data-vi="${i}">${renderValue(_fieldsForView(rec.field_values, v), eopts)}</div>`).join("");
  return `<div class="subtabs">${tabs}</div><div class="subpanels">${panels}</div>`;
}

// ── the paper panel ────────────────────────────────────────────────────────────
// Shown ONCE above the entries: (a) paper identity (editable → the paper record),
// (b) the preset's declared paper-level fields (editable → paper_metadata, audited),
// (c) legacy: any field identical across all entries (editable → propagates to every entry),
// (d) evidence the model never tied to an entry ("Uncited sources").
// Is there anything to put in the Paper card? A preset may declare no paper-level fields at all
// (both MASEMiner presets do), and an import may carry no paper metadata — then the card, and the
// sidebar row that opens it, are an empty box promising something that never arrives.
function paperPanelHasContent() {
  const p = DATA.paper || {}, pm = DATA.paper_metadata || {};
  const hasIdent = p.title || (Array.isArray(p.authors) ? p.authors.length : p.authors) || p.year || p.journal || p.doi;
  const extraKeys = VM.legacy ? [] : undeclared(VM, "paper", pm).filter((k) => typeof pm[k] !== "object" || pm[k] === null);
  return !!(hasIdent || Object.keys(CONSTANT).length || VM.paper.fields.length || extraKeys.length || orphanEvidence().length);
}

function renderPaperPanel(panel) {
  const p = DATA.paper || {};
  const pm = DATA.paper_metadata || {};
  const paperFields = VM.paper.fields;
  const extraKeys = VM.legacy ? [] : undeclared(VM, "paper", pm).filter((k) => typeof pm[k] !== "object" || pm[k] === null);
  const hasConst = Object.keys(CONSTANT).length > 0;
  const orphans = orphanEvidence();
  if (!paperPanelHasContent()) return;
  const box = document.createElement("details");
  box.className = "study-block"; box.open = VM.paperPanel !== "collapsed";
  const identRows = [
    identRow("Title", "title", p.title, false),
    identRow("Authors", "authors", Array.isArray(p.authors) ? p.authors.join("; ") : (p.authors || ""), false),
    identRow("Year", "year", p.year, true),
    identRow("Venue", "journal", p.journal, false),
    p.doi ? `<div class="rv-row"><div class="rv-key">DOI</div><div class="rv-val"><span class="rv-cell">${esc(p.doi)}</span></div></div>` : "",
  ].join("");
  let body = `<div class="rv-root"><div class="rv-obj">${identRows}</div></div>`;
  if (paperFields.length) {
    const pgroups = Object.values(VM.groups).filter((g) => g.scope === "paper");
    const badges = pgroups.map((g) => renderConfBadge(g.id, g, (DATA.paper_confidence || {})[g.id], VM.levels)).join("");
    body += `<div class="paper-fields">${badges ? `<div class="group-head">${badges}</div>` : ""}`
      + `<div class="rv-root">${renderFields(paperFields, pm, "", { editable: true })}</div></div>`;
  }
  if (extraKeys.length) {
    const extra = {}; extraKeys.forEach((k) => (extra[k] = pm[k]));
    body += `<div class="study-shared"><div class="study-sub">Other paper-level values</div>`
      + `${renderValue(extra, { editable: false })}</div>`;
  }
  if (hasConst) {
    body += `<div class="study-shared"><div class="study-sub">Shared across all ${(DATA.records || []).length} entries</div>`
      + `${renderValue(CONSTANT, { editable: true, fieldTypes: fieldTypes() })}</div>`;
  }
  if (orphans.length) {
    body += `<div class="study-shared"><div class="study-sub">Uncited sources — ${orphans.length}</div><div class="uncited-list">`
      + orphans.map(({ ev, i }) => `<button type="button" class="ev-cite" data-eid="${i}" data-page="${ev.page || 1}" title="${esc(ev.snippet || "")}">`
        + `p.${ev.page || "?"}${ev.source ? ` · ${esc(ev.source)}` : ""} · ${esc(String(ev.snippet || "").slice(0, 40))}${(ev.snippet || "").length > 40 ? "…" : ""}</button>`).join("")
      + `</div></div>`;
  }
  box.innerHTML = `<summary>📄 Paper</summary><div class="study-body">${body}</div>`;
  panel.appendChild(box);
  wireStudyIdentity(box);
  const pf = box.querySelector(".paper-fields");
  if (pf) {
    wireControls(pf, savePaperPath, true);
    wirePaperTableRows(pf, () => renderPanel());
    wireConfBadges(pf);
    linkPaperCells(pf);
  }
  const shared = box.querySelector(".study-shared .rv-root");
  if (shared && hasConst) wireControls(shared.closest(".study-shared"), saveStudyField, true);   // a constant edit → every entry
  if (VM.legacy) linkValueCells(box, DATA.evidence.map((ev, i) => ({ ev, i })));   // legacy: constant fields share evidence
  box.querySelectorAll(".uncited-list .ev-cite").forEach((b) => {
    b.onclick = () => jumpToEvidence(+b.dataset.page, +b.dataset.eid);
    b.onmouseenter = () => showEvidence(+b.dataset.eid);
    b.onmouseleave = () => hideEvidence(+b.dataset.eid);
  });
}

// one editable paper-identity row (persists to the paper record on blur)
function identRow(label, field, value, isNum) {
  const v = value == null ? "" : String(value);
  return `<div class="rv-row"><div class="rv-key">${esc(label)}</div>`
    + `<div class="rv-val"><span class="rv-editable study-ident${isNum ? " rv-num" : ""}"`
    + ` contenteditable="plaintext-only" data-field="${esc(field)}">${esc(v)}</span></div></div>`;
}

function wireStudyIdentity(box) {
  box.querySelectorAll(".study-ident").forEach((cell) => {
    cell.dataset.orig = cell.textContent;
    cell.addEventListener("blur", () => {
      const now = cell.textContent.trim();
      if (now === (cell.dataset.orig || "").trim()) return;
      const field = cell.dataset.field;
      let val = now;
      if (field === "year") val = now === "" ? null : (isNaN(Number(now)) ? now : Number(now));
      else if (field === "authors") val = now ? now.split(";").map((s) => s.trim()).filter(Boolean) : [];
      cell.classList.add("rv-edited");
      api.updatePaper(DATA.document_id, { [field]: val })
        .then((r) => { cell.dataset.orig = cell.textContent; if (r && r.paper) DATA.paper = r.paper; })
        .catch((e) => alert("save failed: " + e.message));
    });
  });
}

// A declared paper-level field edit → paper_metadata.<name> on THIS document (audited).
function savePaperField(path, value) {
  return api.updatePaper(DATA.document_id, { fields: { [path]: value } })
    .then((r) => { if (r && r.paper_metadata) DATA.paper_metadata = r.paper_metadata; })
    .catch((e) => alert("save failed: " + e.message));
}

// A cell inside a paper-level TABLE ("statements[2].text"): the API takes declared field names,
// so the whole table goes, with the cell set on the page's copy.
function savePaperPath(path, value) {
  const m = /^([A-Za-z_][A-Za-z0-9_]*)(.*)$/.exec(String(path));
  if (!m || !m[2]) return savePaperField(path, value);
  const pm = JSON.parse(JSON.stringify(DATA.paper_metadata || {}));
  setByPath(pm, path, value);
  return savePaperField(m[1], pm[m[1]]);
}
// ＋ row / ✕ on a paper-level table: the table is saved whole, then the view redrawn.
function wirePaperTableRows(box, rerender) {
  box.querySelectorAll(".rv-addrow").forEach((b) => (b.onclick = async () => {
    const name = b.dataset.path;
    const pm = DATA.paper_metadata || {};
    const rows = Array.isArray(pm[name]) ? JSON.parse(JSON.stringify(pm[name])) : [];
    rows.push(JSON.parse(b.dataset.blank || "{}"));
    await savePaperField(name, rows); rerender();
  }));
  box.querySelectorAll(".rv-rowdel").forEach((b) => (b.onclick = async () => {
    const name = b.dataset.path, i = Number(b.dataset.row);
    const pm = DATA.paper_metadata || {};
    if (!Array.isArray(pm[name]) || !(i in pm[name])) return;
    if (!confirm("Remove this row? The change is logged.")) return;
    await savePaperField(name, pm[name].filter((_, k) => k !== i)); rerender();
  }));
}

// A study-constant edit writes to EVERY record (one call) + syncs in-memory records/CONSTANT.
function saveStudyField(key, value) {
  (DATA.records || []).forEach((r) => { if (r.field_values) r.field_values[key] = value; });
  CONSTANT[key] = value;
  return api.setDocumentField(DATA.document_id, key, value)
    .catch((e) => alert("save failed: " + e.message));
}

// paper-level cells link to paper_metadata.<field> evidence
function linkPaperCells(box) {
  box.querySelectorAll("[data-path]").forEach((cell) => {
    const p = cell.dataset.path;
    const ids = (DATA.evidence || []).map((ev, i) => ({ ev, i }))
      .filter(({ ev }) => ev.field_path === `paper_metadata.${p}`).map(({ i }) => i);
    const def = (VM.fieldIndex.get(p) || {}).field;
    if (ids.length) {
      cell.classList.add(cell.classList.contains("rv-editable") ? "rv-cited" : "rv-linked");
      cell.addEventListener("mouseenter", () => showEvidence(ids));
      cell.addEventListener("mouseleave", () => hideEvidence(ids));
      cell.addEventListener("click", () => verifyAndJump(cell, { ids, page: DATA.evidence[ids[0]].page, exact: true }));
    } else if (def && def.evidence === "value" && (DATA.paper_metadata || {})[p] != null && cell.classList.contains("rv-editable")) {
      cell.classList.add("rv-uncited");
    }
  });
}

// ── the panel ──────────────────────────────────────────────────────────────────
function docProgress() {
  const recs = DATA.records || [];
  const reviewed = recs.filter((r) => r.verification_status === "verified" || r.verification_status === "flagged").length;
  const flagged = recs.filter((r) => r.verification_status === "flagged").length;
  return { reviewed, total: recs.length, flagged, done: recs.length > 0 && reviewed === recs.length };
}

function renderPanel() {
  computeConstant();
  setStageControl(stagesOpen());
  applyPanelWidth();
  const panel = $("#panelbody");
  const pr = docProgress();
  const label = (DATA.field_defs && DATA.field_defs.label) || DATA.schema_id || "—";
  const triage = (DATA.records || []).length > 1
    ? `<select class="triage" id="triage" title="order of the entries">`
      + [["order", "document order"], ["low_conf", "needs attention first"], ["unverified", "unverified first"], ["flagged", "flagged first"]]
        .map(([v, t]) => `<option value="${v}"${TRIAGE === v ? " selected" : ""}>${t}</option>`).join("") + `</select>` : "";
  panel.innerHTML = `<div class="panelhead"><span><span class="progress${pr.done ? " done" : ""}" title="${esc(label)}">`
    + `${pr.done ? "✓ Document reviewed" : `${pr.reviewed}/${pr.total} reviewed${pr.flagged ? ` · ${pr.flagged} flagged` : ""}`}</span>`
    + ` <code title="${esc(DATA.schema_id || "")}">${esc(label)}</code> ${triage}</span>`
    + `<span class="dlbtns">`
    + (reviewSpec() && !GRID && !RAW ? `<button class="btn btn-ghost" id="chaintoggle" title="${CHAIN_OFF ? reviewToggleTitle() : "every field, editable"}">${CHAIN_OFF ? reviewToggleLabel() : "▤ Fields"}</button>` : "")
    + `<button class="btn btn-ghost" id="gridtoggle" title="spreadsheet view of all records">${GRID ? "▤ Cards" : "▦ Grid"}</button>`
    + `<button class="btn btn-ghost" id="rawtoggle">${RAW ? "◫ Rendered" : "{ } Raw"}</button>`
    + `<span class="jobwait" id="jobwait" hidden><span class="spin"></span> <span id="jobwait-txt">Extracting…</span></span>`
    + (PROJECT   // already a dataset: nothing to save, so this simply opens its overview (audit report, dashboards, export, publishing)
        ? `<a class="btn btn-ghost" id="dlsave" href="/dataset?id=${encodeURIComponent(PROJECT)}" title="the dataset overview: audit report, dashboards, export, publishing — and finalising">Open dataset ↗</a>`
        : `<button class="btn btn-primary" id="dlsave" title="keep these papers as a dataset in your workspace; you can carry on reviewing afterwards">💾 Save</button>`)
    + `<button class="btn btn-ghost" id="dljson" title="this paper">⬇ JSON</button>`
    + `<button class="btn btn-ghost" id="dlcsv" title="this paper">⬇ CSV</button>`
    // the whole dataset from here, so a coder never has to leave the review to take the work away
    + (PROJECT ? `<details class="addmenu" id="dsdl"><summary class="btn btn-ghost" title="every paper of this dataset as one file">⬇ Dataset ▾</summary>`
        + `<div class="addmenu-pop"><a href="#" data-dl="json-all">All papers · JSON</a><a href="#" data-dl="csv-all">All papers · CSV</a>`
        + `<a href="#" data-dl="json-reviewed">Reviewed records only · JSON</a><a href="#" data-dl="csv-reviewed">Reviewed records only · CSV</a></div></details>` : "")
    + `<button class="btn btn-ghost" id="addfinding" title="add a manual ${esc(VM.entries.label.toLowerCase())}">＋ ${esc(VM.entries.label)}</button>`
    + `<button class="btn btn-ghost" id="deldoc" title="delete this document + its PDF/pages">🗑</button></span></div>`;
  if (RAW) {                            // Raw: ONE consolidated response, not a block per entry
    const raw = {
      paper_metadata: DATA.paper_metadata || DATA.paper || null,
      [VM.entries.key]: (DATA.records || []).map((r) => ({ ...stripRowIds(r.field_values), ...(Object.keys(r.confidence || {}).length ? { confidence: r.confidence } : {}) })),
      evidence: DATA.evidence || [],
    };
    const box = document.createElement("div");
    box.className = "record";
    box.innerHTML = `<div class="rectitle">stored extraction <span class="muted" style="font-weight:400">(rebuilt from the stored records and evidence)</span>`
      + `<button class="btn btn-ghost" id="dlrawresp" title="the model's verbatim response, exactly as stored at extraction time">⬇ model response</button></div>`
      + `<pre class="rawjson">${esc(JSON.stringify(raw, null, 2))}</pre>`;
    panel.appendChild(box);
    $("#dlrawresp").onclick = async (e) => {
      const b = e.currentTarget;
      const t = await api.rawResponse(DATA.document_id);
      if (t == null) { b.textContent = "no stored response (extracted before responses were kept)"; b.disabled = true; return; }
      download(`${baseName()}-model-response.txt`, t, "text/plain");
    };
    wirePanelHead();
    return;
  }
  // the staged layout draws a paper with no entries too: a blind coding starts from the evidence
  if (stagesSpec() && !CHAIN_OFF && !GRID) { renderStagesPanel(panel, stagesSpec()); wirePanelHead(); renderDocTabs(); return; }
  if (!DATA.records.length) {           // 0-record doc: clear empty state + hand-entry button
    renderPaperPanel(panel);            // paper info above the empty state
    const box = document.createElement("div");
    box.className = "empty-records";
    const confirmed = DATA._sentinel && DATA._sentinel.verification_status === "verified";
    const screened = DATA._screened
      ? `<p class="muted" style="padding:0 2px 8px">✓ Recorded in the dataset as <b>screened — no applicable records</b>.</p>` : "";
    // For a screened paper the reviewer can CONFIRM there are genuinely no entries (verify
    // the sentinel) — distinct from the AI just not finding any.
    const confirmBtn = DATA._sentinel
      ? (confirmed
          ? `<span class="status verified" id="noconfirm" title="a reviewer confirmed this paper has no applicable records">✓ Confirmed — no records</span>`
          : `<button class="btn btn-ghost" id="noconfirm">✓ Confirm — no records here</button>`)
      : "";
    box.innerHTML = screened
      + `<p class="muted" style="padding:8px 2px 12px">No ${esc(VM.entries.label.toLowerCase())} records were extracted from this document — nothing matched the `
      + `<code>${esc(label)}</code> preset (this paper may not contain the kind of data it targets). `
      + `You can still enter the data by hand, or re-process with a different preset.</p>`
      + `<div class="empty-actions"><button class="btn btn-primary" id="empty-add">＋ Add a ${esc(VM.entries.label.toLowerCase())} manually</button>`
      + confirmBtn + `</div>`;
    panel.appendChild(box);
    wirePanelHead();
    const ea = $("#empty-add"); if (ea) ea.onclick = doAddFinding;   // seed a blank editable entry
    const nc = $("#noconfirm");
    if (nc && DATA._sentinel && !confirmed) nc.onclick = () => confirmNoRecords(nc);
    return;
  }
  if (GRID) { renderPaperPanel(panel); renderGridInto(panel); wirePanelHead(); return; }
  if (chainSpec() && !CHAIN_OFF) { renderChainPanel(panel, chainSpec()); wirePanelHead(); renderDocTabs(); return; }
  if (evidenceSpec() && !CHAIN_OFF) { renderEvidencePanel(panel, evidenceSpec()); wirePanelHead(); renderDocTabs(); return; }
  const recs = DATA.records;
  if (recs.length > 1) {                // many entries (e.g. one per table) → view one at a time
    const paperTab = paperPanelHasContent();
    if (PANEL_SEL === null) PANEL_SEL = TRIAGE === "order" && paperTab ? "paper" : orderedEntries(VM, EV, recs, TRIAGE, DATA.issues)[0].i;
    if (PANEL_SEL === "paper" && !paperTab) PANEL_SEL = orderedEntries(VM, EV, recs, TRIAGE, DATA.issues)[0].i;
    renderEntryNav(panel, recs);
    if (PANEL_SEL === "paper" || !recs[PANEL_SEL]) { renderPaperPanel(panel); setContextEvidence(null); }
    else { renderRecordCard(panel, recs[PANEL_SEL]); setContextEvidence(EV.byRecord.get(recs[PANEL_SEL].id) || []); }
  } else {
    renderPaperPanel(panel);
    recs.forEach((rec) => renderRecordCard(panel, rec));
    setContextEvidence(EV.byRecord.get(recs[0].id) || []);
  }
  wirePanelHead();
  renderDocTabs();                       // the sidebar's sub-list follows the selection / statuses
}

// A sub-navigation above the entries: "Paper" + one entry per record, titled by the
// preset's template, in triage order, each with its review status and worst confidence.
function renderEntryNav(panel, recs) {
  const nav = document.createElement("div");
  nav.className = "rnav";
  const ordered = orderedEntries(VM, EV, recs, TRIAGE, DATA.issues);
  nav.innerHTML = (paperPanelHasContent()
      ? `<button class="rnav-tab${PANEL_SEL === "paper" ? " active" : ""}" data-sel="paper">📄 Paper</button>` : "")
    + ordered.map(({ rec, i }) => {
      const worst = worstLevel(VM, { ...(rec.confidence || {}), ...Object.assign({}, ...Object.values(rec.child_confidence || {})) });
      const needs = entryNeeds(VM, EV, rec, DATA.issues);
      return `<button class="rnav-tab${PANEL_SEL === i ? " active" : ""}" data-sel="${i}" title="${esc(rec.verification_status)}${needs ? ` · ${needs} to check` : ""}">`
        + `${esc(entryTitle(VM, rec, i))}<span class="st-dot ${esc(rec.verification_status)}"></span>`
        + (worst ? renderConfDot(worst, VM.levels, `lowest confidence: ${worst}`) : "")
        + (needs && rec.verification_status !== "verified" ? `<span class="needs">${needs}</span>` : "") + `</button>`;
    }).join("");
  panel.appendChild(nav);
  nav.querySelectorAll(".rnav-tab").forEach((b) => (b.onclick = () => selectEntry(b.dataset.sel === "paper" ? "paper" : +b.dataset.sel)));
}

function selectEntry(sel) {
  PANEL_SEL = sel;
  renderPanel();
  const rec = sel !== "paper" ? (DATA.records || [])[sel] : null;
  // a single-entry paper shows paper + card together: scroll to the part that was picked
  const target = rec ? document.querySelector(`.record[data-rid="${rec.id}"]`) : document.querySelector("#panel .study-block");
  if (target && target.scrollIntoView) target.scrollIntoView({ block: "start", behavior: "smooth" });
  if (rec) jumpToEntry(rec);
}

// Selecting an entry jumps to it in the PDF: its own identifying citation (field "key[i]")
// when the model gave one, else its first evidence.
function jumpToEntry(rec) {
  const ids = EV.entryCites.get(rec.id) || EV.byRecord.get(rec.id) || [];
  if (!ids.length) return;
  jumpToEvidence(DATA.evidence[ids[0]].page, ids[0]);
}

function renderRecordCard(panel, rec) {
  const card = document.createElement("div");
  card.className = "record"; card.dataset.rid = rec.id;
  const worst = worstLevel(VM, { ...(rec.confidence || {}), ...Object.assign({}, ...Object.values(rec.child_confidence || {})) });
  const myIssues = (DATA.issues || []).filter((i) => typeof i.path === "string" && i.path.startsWith(`${VM.entries.key}[${rec.entry_index}]`) && i.code !== "uncited_value" && i.code !== "uncited_row");
  card.innerHTML =
    `<div class="rectitle">${esc(entryTitle(VM, rec, rec.entry_index))}`
    + `<span class="status ${rec.verification_status}">${rec.verification_status}</span>`
    + (worst ? renderConfDot(worst, VM.levels, `lowest confidence: ${worst}`) : "")
    + `<button class="histbtn" title="change history">↻ history</button>`
    + `<button class="recdel" title="delete this ${esc(VM.entries.label.toLowerCase())}">🗑</button></div>`
    + (myIssues.length ? `<div class="issue-note" title="${esc(myIssues.map((i) => `${i.path}: ${i.message}`).join("\n"))}">⚠ ${myIssues.length} structural issue${myIssues.length === 1 ? "" : "s"} in the model output — hover for details</div>` : "")
    + (VM.legacy ? renderLegacyConf(rec) + renderLegacyBody(rec) : renderEntryBody(rec))
    // the verdict comes last: a coder reaches it after every field, not before
    + `<div class="verify verify-foot"><span class="verify-lead muted">Checked every field?</span>`
    + `<button class="vbtn ok${rec.verification_status === "verified" ? " on" : ""}" data-status="verified">✓ verify</button>`
    + `<button class="vbtn flag${rec.verification_status === "flagged" ? " on" : ""}" data-status="flagged">⚑ flag</button></div>`
    + `<div class="histbody" hidden></div>`;
  wireCard(card, rec);
  panel.appendChild(card);
}

// Legacy documents: whatever ratings the old per-sample block carried, as badges.
function renderLegacyConf(rec) {
  const entries = Object.entries(rec.confidence || {});
  if (!entries.length) return "";
  return `<div class="group-head">${entries.map(([gid, r]) => renderConfBadge(gid, VM.groups[gid] || { label: formatKey(gid) }, r, VM.levels)).join("")}</div>`;
}

// display.inline_evidence: under a field, the note of its confidence group (once per group) and the
// quotes that cite it exactly — each with its source label ("supporting", "questioning", a table…)
// and page, a button that jumps to the highlight — so a call is judged next to the model's reading.
function inlineEvidence(rec, f, shown) {
  let note = "";
  const gid = f.confidence;
  if (gid && !shown.has(gid)) {
    shown.add(gid);
    const r = (rec.confidence || {})[gid];
    if (r && (r.level || r.notes))
      note = `<div class="rv-inote">${r.level ? `${renderConfDot(r.level, VM.levels, r.level)} <b>${esc(String(r.level))}</b>` : ""}${r.notes ? `${r.level ? " · " : ""}${esc(r.notes)}` : ""}</div>`;
  }
  const ids = EV.exact.get(`${rec.id}|${f.name}`) || [];
  const quotes = ids.map((i) => {
    const ev = DATA.evidence[i] || {};
    const src = String(ev.source || "quote");
    return `<li><button type="button" class="ev-cite rv-iq" data-eids="${i}" data-page="${ev.page || 1}" title="show in the PDF">`
      + `<span class="rv-stance" data-src="${esc(src.toLowerCase())}">${esc(src)}</span> p. ${ev.page || "?"}</button> <span class="rv-qt">“${esc(ev.snippet || "")}”</span></li>`;
  }).join("");
  return note || quotes ? `<div class="rv-inline">${note}${quotes ? `<ul class="rv-iquotes">${quotes}</ul>` : ""}</div>` : "";
}

// The declared layout: tabs → (group badges → fields → sub-entries) per tab, an "Other"
// tab for anything undeclared the model returned. Every declared field appears, a missing
// one visibly so.
function renderEntryBody(rec) {
  const crit = criteriaSpec();
  if (crit) return renderCriteriaBody(rec, crit);
  const fv = rec.field_values || {};
  const eopts = { editable: true };
  const tabs = VM.tabs.map((t) => {
    const defs = t.fields.map((n) => VM.fieldIndex.get(n)).filter((x) => x && x.scope === "entry").map((x) => x.field);
    const children = t.fields.map((n) => VM.fieldIndex.get(n)).filter((x) => x && x.scope === "child_key").map((x) => x.child);
    // child-scope groups are rated per sub-entry and shown on each card, not on the tab
    const badges = (t.groups || []).filter((gid) => (VM.groups[gid] || {}).scope !== "child")
      .map((gid) => renderConfBadge(gid, VM.groups[gid], (rec.confidence || {})[gid], VM.levels)).join("");
    let html = badges && !VM.inlineEvidence ? `<div class="group-head">${badges}</div>` : "";
    if (defs.length && VM.inlineEvidence) {             // each field, then its note and its quotes
      const shown = new Set();
      html += `<div class="rv-root">${defs.map((f) => renderFields([f], fv, "", eopts) + inlineEvidence(rec, f, shown)).join("")}</div>`;
    } else if (defs.length) html += `<div class="rv-root">${renderFields(defs, fv, "", eopts)}</div>`;
    for (const ch of children) {
      const cites = EV.tableCites.get(`${rec.id}|${ch.key}`);
      html += `<div class="rv-row rv-row-block rv-decl" data-field="${esc(ch.key)}"><div class="rv-key" data-path="${esc(ch.key)}" title="${esc(ch.help || "")}">`
        + `${esc(ch.label || formatKey(ch.key))}${Array.isArray(fv[ch.key]) ? ` <span class="muted">(${fv[ch.key].length})</span>` : ""}`
        + (cites ? ` <button type="button" class="ev-cite" data-eids="${cites.join(",")}" data-page="${DATA.evidence[cites[0]].page || 1}">p.${DATA.evidence[cites[0]].page || "?"}</button>` : "")
        + `</div><div class="rv-val rv-nested">${renderChild(ch, fv[ch.key], eopts)}</div></div>`;
    }
    return { t, html };
  });
  const other = undeclared(VM, "entry", fv);
  if (other.length) {
    const extra = {}; other.forEach((k) => (extra[k] = fv[k]));
    tabs.push({ t: { id: "_extra", label: "Other" }, html: `<p class="muted" style="font-size:12px;margin:0 0 6px">Returned by the model but not declared by the preset.</p>${renderValue(extra, eopts)}` });
  }
  if (tabs.length === 1) return `<div class="subpanels"><div class="subpanel" data-vi="0">${tabs[0].html}</div></div>`;
  const strip = tabs.map(({ t }, i) => {
    const worst = worstLevel(VM, Object.fromEntries((t.groups || []).map((g) => [g, (rec.confidence || {})[g]]).filter(([, v]) => v)));
    return `<button class="subtab${i === 0 ? " active" : ""}" data-vi="${i}">${esc(t.label)}${worst && isLow(VM, worst) ? renderConfDot(worst, VM.levels, "low confidence here") : ""}</button>`;
  }).join("");
  const panels = tabs.map(({ html }, i) => `<div class="subpanel${i === 0 ? "" : " hidden"}" data-vi="${i}">${html}</div>`).join("");
  return `<div class="subtabs">${strip}</div><div class="subpanels">${panels}</div>`;
}

function wirePanelHead() {
  const ct = $("#chaintoggle"); if (ct) ct.onclick = () => { CHAIN_OFF = !CHAIN_OFF; renderPanel(); };
  const dj = $("#dljson"); if (dj) dj.onclick = downloadJSON;
  const dc = $("#dlcsv"); if (dc) dc.onclick = downloadCSV;
  document.querySelectorAll("#dsdl [data-dl]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); $("#dsdl").open = false; downloadDataset(a.dataset.dl); }));
  const save = $("#dlsave"); if (save && !PROJECT) save.onclick = doSave;
  syncJobWait();
  $("#addfinding").onclick = doAddFinding;
  $("#deldoc").onclick = doDelete;
  $("#rawtoggle").onclick = () => { RAW = !RAW; renderPanel(); };
  const g = $("#gridtoggle"); if (g) g.onclick = () => { GRID = !GRID; renderPanel(); };
  const tr = $("#triage"); if (tr) tr.onchange = () => {
    TRIAGE = tr.value; try { localStorage.setItem("metalens_triage", TRIAGE); } catch { /* */ }
    renderPanel();
  };
}

// Cross-record spreadsheet of the current document's records. Rendered in the
// records panel so the PDF stays on the left — clicking a linked cell still jumps
// to (and pinpoint-highlights) its source, exactly like a card value cell.
function renderGridInto(panel) {
  const host = document.createElement("div");
  panel.appendChild(host);
  const order = orderedEntries(VM, EV, DATA.records, TRIAGE, DATA.issues).map(({ rec }) => rec);
  // a declared preset flattens to the unit display.grid_rows names (entry / sub-entry /
  // table row); a legacy document keeps its sub_views columns
  const specMode = !VM.legacy;
  renderGrid(host, {
    records: order,
    ...(specMode ? { columns: gridColumns(VM), rows: gridRows(VM, order) }
                 : { subViews: (DATA.field_defs && DATA.field_defs.sub_views) || [] }),
    evidenceFor: (rec) => recordEvidence(rec)
      .map(({ ev, i }) => ({ i, page: ev.page, path: stripCore(ev.field_path) })),
    confidenceFor: specMode ? (row) => worstLevel(VM, row.conf || {})
                            : (rec) => worstLevel(VM, rec.confidence || {}),
    levels: VM.levels,
    onCellClick: (rec, col, cov, e) => { if (cov) gridCellClick(e.currentTarget, cov); },
    // a grid edit is the same correction as a card edit: logged with original → final
    onCellEdit: specMode ? (rec, path, val) => saveFieldEdit(rec, null, path, val, curVal(rec, path)) : null,
  });
  host.querySelectorAll(".grid-linked[data-eid]").forEach((td) => {
    const eid = +td.dataset.eid;
    td.addEventListener("mouseenter", () => showEvidence(eid));
    td.addEventListener("mouseleave", () => hideEvidence(eid));
  });
}

// Grid cell → same click-to-source as a card value cell (jump + numeric pinpoint),
// minus the sibling "not found" note (which would break table layout → use a title).
async function gridCellClick(cell, cov) {
  jumpToEvidence(cov.page, cov.i);
  const txt = (cell.textContent || "").trim();
  if (!NUM_RE.test(txt)) return;
  try {
    const r = await api.locateValue(DATA.document_id, txt.replace(/%$/, ""), cov.page);
    if (r.no_pdf) return;
    if (r.found) flashRects(r.page || cov.page, r.rects);
    else cell.title = "This exact number isn't in the source PDF text — it may be rounded, transformed, or computed. Not necessarily wrong.";
  } catch { /* best-effort */ }
}

async function doDelete() {
  if (!confirm("Delete this document, its records, and its stored PDF + page images? This cannot be undone.")) return;
  try { await api.deleteDocument(DATA.document_id); location.href = "/workspace"; }
  catch (e) { alert("delete failed: " + e.message); }
}

// re-fetch the sidebar counts + the current document after a record add/delete
async function reloadDoc() {
  try { DOCS = (await api.documents(PROJECT ? { dataset: PROJECT } : {})).documents || []; } catch { /* */ }
  renderDocTabs();
  await load(DOCID);
}

async function doDeleteRecord(rec) {
  if (!confirm(`Delete ${VM.entries.label.toLowerCase()} "${entryTitle(VM, rec, rec.entry_index)}"? This cannot be undone.`)) return;
  try { await api.deleteRecord(rec.id); await reloadDoc(); }
  catch (e) { alert("delete failed: " + e.message); }
}

// Add a manual entry: seed a COMPLETE blank form from the preset's declaration (typed
// blanks: [] for tables / sub-entries / multi, null for booleans) so its cells are
// immediately editable; edits route through the verify layer.
async function doAddFinding(extra) {
  const template = VM.legacy ? {} : seedEntry(VM);
  if (extra && extra.constructor === Object) Object.assign(template, extra);   // a stage's auto id; a click event is not one
  if (VM.legacy) {
    const views = (DATA.field_defs && DATA.field_defs.sub_views) || [];
    views.forEach((v) => (v.include_keys || []).forEach((k) => { if (!(k in template)) template[k] = ""; }));
    (DATA.records || []).forEach((r) => {
      for (const [k, v] of Object.entries(r.field_values || {})) {
        if (k === "evidence" || k === "extraction_confidence" || k === "confidence" || (k in template)) continue;
        template[k] = (v && typeof v === "object") ? (Array.isArray(v) ? [] : {}) : "";
      }
    });
  }
  try { await api.addRecord(DATA.document_id, { field_values: template }); await reloadDoc(); }
  catch (e) { alert("add failed: " + e.message); }
}

// Save ALL in-scope documents (the whole round), not just the highlighted one.
// Only offered outside a project (a project's docs are already in a dataset).
async function doSave() {
  const b = $("#dlsave");
  // Save EVERY paper in view — the loaded docs, PLUS any of this round's jobs that have
  // finished (their document may not have been pulled into DOCS yet), plus the one on
  // screen. Relying on the DOCS snapshot alone is why a just-finished sibling was missed.
  const nrecOf = new Map(DOCS.map((d) => [d.document_id, d.n_records || 0]));
  if (DATA && DATA.document_id) nrecOf.set(DATA.document_id, (DATA.records || []).length);
  const ids = [...new Set([
    ...DOCS.map((d) => d.document_id),
    ...Object.values(JOBS).filter((j) => j.status === "complete" && j.document_id).map((j) => j.document_id),
    ...(DATA && DATA.document_id ? [DATA.document_id] : []),
  ].filter(Boolean))];
  if (!ids.length) { alert("Nothing to save yet."); return; }
  const screened = ids.filter((id) => !((nrecOf.get(id) || 0) > 0)).length;
  if (screened && !confirm(`${screened} of ${ids.length} paper(s) have no extracted records.\n`
      + `They'll be kept as "screened — no records" so the dataset records that they were attempted. Continue?`)) return;
  b.disabled = true;
  try {
    // record the recipe (schema/preset + model + the exact prompt) so re-opening the
    // dataset can add papers with the same preset without re-choosing it.
    const model = (DATA.records || []).map((r) => r.extraction && r.extraction.model).find(Boolean) || null;
    const recipe = { schema_id: DATA.schema_id || null, model, prompt: DATA.prompt || null };
    // no dialog here: the papers are bundled and the overview opens; naming and keeping it happen there
    const base = ids.length > 1 ? `${ids.length} papers` : ((DATA.paper && DATA.paper.title) || (DATA.filename || "").replace(/\.pdf$/i, ""));
    const ds = await saveToWorkspace(ids, { defaultName: base, recipe, silent: true });
    if (ds) {
      if (ds.failed) alert(`${ds.failed} paper(s) could not be included; the rest are in the overview.`);
      location.href = `/dataset?id=${encodeURIComponent(ds.id)}&saved=1`;   // overview: name it, audit report, export, publish
    } else b.disabled = false;
  } catch (e) { alert("save failed: " + e.message); b.disabled = false; }
}

function wireCard(card, rec) {
  const hb = card.querySelector(".histbtn");
  if (hb) hb.onclick = () => toggleHistory(card, rec);
  const rd = card.querySelector(".recdel");
  if (rd) rd.onclick = () => doDeleteRecord(rec);
  card.querySelectorAll(".subtab").forEach((b) => (b.onclick = () => {
    const vi = b.dataset.vi;
    card.querySelectorAll(".subtab").forEach((x) => x.classList.toggle("active", x === b));
    card.querySelectorAll(".subpanel").forEach((p) => p.classList.toggle("hidden", p.dataset.vi !== vi));
  }));
  wireConfBadges(card);
  if (VM.legacy) linkValueCells(card, recordEvidence(rec));
  else linkCells(card, rec);
  card.querySelectorAll(".vbtn").forEach((b) =>
    (b.onclick = () => sendVerify(card, rec, b.dataset.status)));
  // free-text/number cells + typed dropdown & multi-select controls → the verify layer
  const rv = criteriaSpec();
  const blindCalls = rv && rv.blind ? new Set((rv.criteria || []).map((c) => c.call)) : null;
  wireControls(card, (path, val) => {
    const saved = saveFieldEdit(rec, card, path, val, curVal(rec, path));
    if (blindCalls && blindCalls.has(path)) saved.then(() => rerenderCard(card, rec));   // the model's side appears once the call is made
    return saved;
  }, true);
  wireTableRows(card, rec);
}

// Re-render one card in place after a structural edit (a table row added or removed),
// keeping whichever tab the coder had open.
function rerenderCard(card, rec) {
  const active = card.querySelector(".subtab.active");
  const tmp = document.createElement("div");
  renderRecordCard(tmp, rec);
  const fresh = tmp.firstElementChild;
  card.replaceWith(fresh);
  if (active) {
    const b = [...fresh.querySelectorAll(".subtab")].find((x) => x.dataset.vi === active.dataset.vi);
    if (b) b.click();
  }
  return fresh;
}

// ＋ row / ✕ on a typed table (factor loadings, correlations, …) and on a sub-entry list (an
// arm, a result): the paper may report a row the model missed — a whole factor, say — and a
// coder must be able to put it in. Both go through the verify layer as corrections, like every
// other edit. `rerender` redraws the view after the save: the card itself by default, the whole
// panel for a layout whose cells are not inside a record card.
function wireTableRows(card, rec, rerender = () => rerenderCard(card, rec), fill = null) {
  card.querySelectorAll(".rv-addrow").forEach((b) => (b.onclick = async () => {
    const path = b.dataset.path;
    if (!rec.field_values) rec.field_values = {};
    const cur = getByPath(rec.field_values, path);
    const rows = Array.isArray(cur) ? cur : [];
    const blank = JSON.parse(b.dataset.blank || "{}");
    if (fill) Object.assign(blank, fill(path, rows) || {});          // a stage's auto id, from the rows as they stand
    rows.push(blank);
    if (!Array.isArray(cur)) setByPath(rec.field_values, path, rows);
    try {
      await saveWholeRecord(rec, [{ field_path: `${path}[${rows.length - 1}]`, original_value: null, final_value: "(row added)" }]);
      maybeOfferRepublish();
      rerender();
      // inside a quoted attribute selector the brackets are literal — no escaping
      const first = document.querySelector(`#panelbody [data-rid="${rec.id}"] [data-path^="${path}[${rows.length - 1}]."]`);
      if (first && first.focus) first.focus();
    } catch (e) { alert("add failed: " + e.message); }
  }));
  card.querySelectorAll(".rv-rowdel").forEach((b) => (b.onclick = async () => {
    const path = b.dataset.path, i = Number(b.dataset.row);
    const rows = getByPath(rec.field_values || {}, path);
    if (!Array.isArray(rows) || !(i in rows)) return;
    const row = rows[i];
    const label = row && typeof row === "object"
      ? Object.entries(row).filter(([k, x]) => !k.startsWith("_") && x !== null && x !== undefined && x !== "")
          .slice(0, 3).map(([, x]) => String(x)).join(" · ") || "this row"
      : String(row);
    if (!confirm(`Remove ${label}? The change is logged in the record's history.`)) return;
    const live = getByPath(rec.field_values, path);
    live.splice(i, 1);
    try {
      await saveWholeRecord(rec, [{ field_path: `${path}[${i}]`, original_value: label, final_value: "(row removed)" }]);
      maybeOfferRepublish(); rerender();
    } catch (e) { live.splice(i, 0, row); alert("remove failed: " + e.message); }
  }));
}

// A structural edit (a row added or removed) replaces the whole record — taken from the
// page's copy when its turn in the queue comes, so it carries every cell edit before it.
function saveWholeRecord(rec, diff) {
  const after = [...(PENDING.get(rec.id) || [])];
  return track(rec.id, `${rec.id}|*`, Promise.allSettled(after).then(() => api.verify(rec.id, {
    status: "corrected", diff, field_values: JSON.parse(JSON.stringify(rec.field_values || {})) })));
}

// A badge's notes open on click — the notes are what tell the coder what to check.
function wireConfBadges(root) {
  root.querySelectorAll("button.conf-badge").forEach((b) => (b.onclick = () => {
    const notes = b.nextElementSibling;
    if (!notes || !notes.classList.contains("conf-notes")) return;
    notes.hidden = !notes.hidden;
    b.setAttribute("aria-expanded", String(!notes.hidden));
  }));
}

async function sendVerify(card, rec, status, notes) {
  card.querySelectorAll(".vbtn").forEach((b) => (b.disabled = true));
  try {
    await api.verify(rec.id, notes === undefined ? { status } : { status, notes });
    if (notes !== undefined) rec.note = notes ? { text: notes, status, by: "you", at: new Date().toISOString() } : null;
    maybeOfferRepublish();
    rec.verification_status = status;
    setStatus(card, status);
    refreshNavState();
    if (AUTOADV && (DATA.records || []).length > 1) nextEntry(true);
  }
  catch (e) { alert("verify failed: " + e.message); }
  finally { card.querySelectorAll(".vbtn").forEach((b) => (b.disabled = false)); }
}

// keep the nav dots + progress in step without re-rendering the whole panel
function refreshNavState() {
  const pr = docProgress();
  const el = $(".progress");
  if (el) {
    el.classList.toggle("done", pr.done);
    el.textContent = pr.done ? "✓ Document reviewed" : `${pr.reviewed}/${pr.total} reviewed${pr.flagged ? ` · ${pr.flagged} flagged` : ""}`;
  }
  document.querySelectorAll(".rnav-tab[data-sel]").forEach((b) => {
    if (b.dataset.sel === "paper") return;
    const rec = (DATA.records || [])[+b.dataset.sel]; if (!rec) return;
    const dot = b.querySelector(".st-dot"); if (dot) dot.className = `st-dot ${rec.verification_status}`;
  });
  const me = DOCS.find((d) => d.document_id === DOCID);
  if (me) {
    const recs = DATA.records || [];
    me.n_verified = recs.filter((r) => r.verification_status === "verified").length;
    me.n_flagged = recs.filter((r) => r.verification_status === "flagged").length;
    const tab = document.querySelector(`#doctabs .doctab[data-id="${DOCID}"]`);
    if (tab) { tab.classList.remove("reviewed", "flagged"); const s = docShade(me); if (s) tab.classList.add(s); }
  }
}

// Move to the next entry in triage order (skipping reviewed ones when `unreviewedOnly`).
function nextEntry(unreviewedOnly, dir = 1) {
  const recs = DATA.records || [];
  if (recs.length < 2) return;
  const order = orderedEntries(VM, EV, recs, TRIAGE, DATA.issues).map(({ i }) => i);
  const cur = PANEL_SEL === "paper" || PANEL_SEL === null ? -1 : order.indexOf(PANEL_SEL);
  for (let step = 1; step <= order.length; step++) {
    const j = order[(cur + dir * step + order.length * step) % order.length];
    const st = recs[j].verification_status;
    if (!unreviewedOnly || (st !== "verified" && st !== "flagged")) { selectEntry(j); return; }
  }
}

// Keyboard flow: j/k next/prev entry, n next unreviewed, v verify, f flag, 1-9 tabs, ? help.
function mountKeys() {
  document.addEventListener("keydown", (e) => {
    if (!DATA || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName))) return;
    const chain = chainSpec() && !CHAIN_OFF && !GRID && !RAW;
    const staged = stagesSpec() && !CHAIN_OFF && !GRID && !RAW;
    const card = (chain && document.querySelector(".chain-card.cur[data-rid]")) || document.querySelector(".record[data-rid]");
    if (staged && (e.key === "]" || e.key === "[")) { stageStep(e.key === "]" ? 1 : -1); e.preventDefault(); }
    else if (staged && (e.key === "ArrowRight" || e.key === "ArrowLeft")) { stageCursor(STAGE_CUR + (e.key === "ArrowRight" ? 1 : -1)); e.preventDefault(); }
    else if (chain && (e.key === "j" || e.key === "]" || e.key === "k" || e.key === "[")) { chainStep(e.key === "j" || e.key === "]" ? 1 : -1); e.preventDefault(); }
    else if (e.key === "j" || e.key === "]") { nextEntry(false, 1); e.preventDefault(); }
    else if (e.key === "k" || e.key === "[") { nextEntry(false, -1); e.preventDefault(); }
    else if (e.key === "n") { nextEntry(true, 1); e.preventDefault(); }
    else if (e.key === "v" && card) { const b = card.querySelector('.vbtn[data-status="verified"]'); if (b) b.click(); }
    else if (e.key === "f" && card) { const b = card.querySelector('.vbtn[data-status="flagged"]'); if (b) b.click(); }
    else if (/^[1-9]$/.test(e.key) && card) { const b = card.querySelectorAll(".subtab")[+e.key - 1]; if (b) b.click(); }
    else if (e.key === "?") {
      const old = $(".kbd-help"); if (old) { old.remove(); return; }
      const h = document.createElement("div"); h.className = "kbd-help";
      h.innerHTML = `<b>Keys</b> · <kbd>j</kbd>/<kbd>k</kbd> next/prev entry · <kbd>n</kbd> next unreviewed · <kbd>v</kbd> verify · <kbd>f</kbd> flag · <kbd>1</kbd>–<kbd>9</kbd> tab${staged ? " · <kbd>]</kbd>/<kbd>[</kbd> next/prev stage · <kbd>→</kbd>/<kbd>←</kbd> next/prev item" : ""} · <kbd>?</kbd> close`
        + `<br><label style="font-size:12px"><input type="checkbox" id="kbd-auto"${AUTOADV ? " checked" : ""}/> auto-advance after verify / flag</label>`;
      document.body.appendChild(h);
      $("#kbd-auto").onchange = (ev) => { AUTOADV = ev.target.checked; try { localStorage.setItem("metalens_autoadvance", AUTOADV ? "1" : "0"); } catch { /* */ } };
    }
  });
}

// Confirm a screened (0-record) paper genuinely has no entries — verify its sentinel.
function confirmNoRecords(btn) {
  if (!DATA._sentinel) return;
  btn.disabled = true;
  api.verify(DATA._sentinel.id, { status: "verified" })
    .then(() => { DATA._sentinel.verification_status = "verified"; renderPanel(); })
    .catch((e) => { btn.disabled = false; alert("save failed: " + e.message); });
}

function setStatus(card, status) {
  const badge = card.querySelector(".status");
  badge.textContent = status; badge.className = `status ${status}`;
  card.querySelectorAll(".verify-foot .vbtn").forEach((b) => b.classList.toggle("on", b.dataset.status === status));
}

// ── the chain layout: a preset-declared review of claims as cause → effect edges ────────────
// Declared by display.review (layout "chain"): which entry fields are the two ends and the sign,
// which fields hold the quotes (abstract, introduction), which child holds the results that carry
// the claim. Drawn per paper: a small graph of its claims, then one card per claim with the quotes
// (each a jump into the PDF), the results with the cross-check verdict, and OK / Flag.
function chainSpec() {
  const rv = VM && !VM.legacy && VM.spec && VM.spec.display ? VM.spec.display.review : null;
  return rv && rv.layout === "chain" ? rv : null;
}
function criteriaSpec() {
  const rv = VM && !VM.legacy && VM.spec && VM.spec.display ? VM.spec.display.review : null;
  return rv && rv.layout === "criteria" ? rv : null;
}
// "AI as the treatment: why changed" next to the "AI as the treatment" row reads "why changed"
const shortLabel = (f, call) => { const l = f.label || formatKey(f.name), c = call.label || formatKey(call.name); return l.startsWith(c + ":") ? l.slice(c.length + 1).trim() : l; };
const certaintyWord = (p) => { const c = Math.abs(p - 0.5) * 2; return c >= 0.8 ? "certain" : c >= 0.4 ? "fairly certain" : "uncertain"; };

// display.review.layout "criteria" (a screening): one row per criterion — its label, the call
// (editable, the model's prefill), the model's probability as a chip shaded from green (certain)
// to red (a coin toss) — then the model's reasoning and the quotes for and against the call, each a
// button that jumps to the highlight. The fields the layout names follow; everything else folds.
function renderCriteriaBody(rec, rv) {
  const fv = rec.field_values || {}, eopts = { editable: true };
  // the evidence source labels of each side: a string or a list; "questioning" is read as against
  const lower = (x) => (Array.isArray(x) ? x : [x]).map((s) => String(s).toLowerCase());
  const sides = { for: ["supporting"], against: ["contradictory", "questioning"], ...(rv.sides || {}) };
  const forL = lower(sides.for), agL = lower(sides.against);
  const blind = !!rv.blind;
  const mixed = rv.quotes === "mixed";          // no sides, no labels: the quotes alone, in a fixed random order
  // a small seeded shuffle, so the order is random but the same every time this record is drawn
  const shuffled = (list, seed) => { let x = 0; for (const ch of String(seed)) x = (x * 31 + ch.charCodeAt(0)) >>> 0;
    const out = list.slice(); for (let i = out.length - 1; i > 0; i--) { x = (x * 1664525 + 1013904223) >>> 0; const j = x % (i + 1); [out[i], out[j]] = [out[j], out[i]]; } return out; };
  const def = (n) => { const x = n && VM.fieldIndex.get(n); return x && x.scope === "entry" ? x.field : null; };
  const used = new Set();
  // both sides are always listed, so an empty one is read as "the model quoted nothing against it",
  // not as a list that failed to render
  const quoteList = (label, title, list, always) => list.length ? `<div class="cr-side cr-${label}"><b>${title}</b><ul class="rv-iquotes">`
    + list.map(({ i, ev }) => `<li><button type="button" class="ev-cite rv-iq" data-eids="${i}" data-page="${ev.page || 1}" title="show in the PDF">p. ${ev.page || "?"}</button> <span class="rv-qt">“${esc(ev.snippet || "")}”</span></li>`).join("") + `</ul></div>`
    : always ? `<div class="cr-side cr-${label} cr-empty"><b>${title}</b><span class="muted">none quoted</span></div>` : "";
  const rows = (rv.criteria || []).map((c) => {
    const f = def(c.call); if (!f) return "";
    [c.call, c.probability, c.reasoning, c.overturn, ...(c.hidden || [])].forEach((n) => n && used.add(n));
    const p = c.probability && fv[c.probability] != null && fv[c.probability] !== "" ? Number(fv[c.probability]) : NaN;
    // blind: the coder calls first; the model's probability, reasoning and the why-differ field appear after
    const called = fv[c.call] != null && fv[c.call] !== "";
    const veiled = blind && !called;
    const chip = veiled ? `<span class="cr-p cr-veiled" title="the model's probability appears once you have made your call">?</span>`
      : Number.isFinite(p) ? `<span class="cr-p" style="--cert:${Math.min(1, Math.abs(p - 0.5) * 2).toFixed(2)}" title="the model's probability that the criterion is met: ${p.toFixed(2)} — ${certaintyWord(p)}">${p.toFixed(2)}</span>` : "";
    const reason = veiled ? null : (c.reasoning ? fv[c.reasoning] : null);
    const ot = veiled ? null : def(c.overturn);
    const quotes = (EV.exact.get(`${rec.id}|${c.call}`) || []).map((i) => ({ i, ev: DATA.evidence[i] || {} }));
    const src = (q) => String(q.ev.source || "").toLowerCase();
    const ev = mixed ? quoteList("mixed", "Quotes", shuffled(quotes, `${rec.id}|${c.call}`), true)
      : quoteList("for", "Supports", quotes.filter((q) => forL.includes(src(q))), true) + quoteList("against", "Contradicts", quotes.filter((q) => agL.includes(src(q))), true)
      + quoteList("other", "Quotes", quotes.filter((q) => !forL.includes(src(q)) && !agL.includes(src(q))));
    return `<div class="cr" data-field="${esc(c.call)}"${f.confidence ? ` data-group="${esc(f.confidence)}"` : ""}>
      <div class="cr-head"><div class="cr-label" title="${esc(f.help || "")}">${esc(f.label || formatKey(f.name))}</div>
        <div class="cr-call">${renderFields([f], fv, "", eopts)}</div>${chip}
        ${ot ? `<div class="cr-ot" title="${esc(ot.help || "")}"><span class="muted">${esc(shortLabel(ot, f))}</span>${renderFields([ot], fv, "", eopts)}</div>` : ""}</div>
      ${veiled ? `<div class="cr-reason cr-veiled-note">Your call first: the model's probability and reasoning appear once you have chosen.</div>` : ""}
      ${reason != null && String(reason).trim() ? `<div class="cr-reason">${esc(String(reason))}</div>` : ""}
      <div class="cr-ev">${ev}</div></div>`;
  }).join("");
  const shown = new Set();
  const after = (rv.fields || []).map(def).filter(Boolean);
  after.forEach((f) => used.add(f.name));
  const rest = ((VM.entries && VM.entries.fields) || []).filter((f) => !used.has(f.name));
  return `<div class="subpanels"><div class="subpanel" data-vi="0"><div class="cr-list">${rows}</div>`
    + (after.length ? `<div class="rv-root cr-after">${after.map((f) => renderFields([f], fv, "", eopts) + inlineEvidence(rec, f, shown)).join("")}</div>` : "")
    + (rest.length ? `<details class="cr-more"><summary>More <span class="muted">(${rest.length} field${rest.length === 1 ? "" : "s"})</span></summary><div class="rv-root">${renderFields(rest, fv, "", eopts)}</div></details>` : "")
    + `</div></div>`;
}
const SIGN_CLASS = { "+": "cs-plus", "-": "cs-minus", "0": "cs-zero", "mixed": "cs-mixed" };
const SIGN_GLYPH = { "+": "+", "-": "−", "0": "0", "mixed": "±" };

async function loadChainAux(docId) {
  const ds = PROJECT || ((DATA.records || [])[0] || {}).dataset_id || null;
  if (!ds) { AUX = { dataset: null, cross: null, concepts: null }; return; }
  if (AUX.dataset === ds && AUX.cross !== undefined) { /* refresh anyway: verdicts follow edits */ }
  const [cross, vocabs] = await Promise.all([api.crosscheck(ds).catch(() => null), api.vocabularies(ds).catch(() => ({ vocabularies: [] }))]);
  let concepts = null;
  if ((vocabs.vocabularies || []).some((v) => v.status === "committed")) {
    try {
      const t = await api.analysisTable(ds, "entries");
      const at = Object.fromEntries(t.columns.map((c, k) => [c.name, k]));
      concepts = {};
      for (const row of t.rows) {
        const rec = t.records[row.r]; const m = {};
        for (const c of t.columns) if (c.scope === "vocabulary" && c.name.endsWith("_concept")) m[c.name] = row.v[at[c.name]];
        concepts[rec.id] = m;
      }
    } catch { concepts = null; }
  }
  const byKey = {};
  for (const r of (cross && cross.rows) || []) byKey[`${r.record_id}|${r.path}`] = r;
  AUX = { dataset: ds, cross: cross && cross.companion ? byKey : null, concepts };
  if (DOCID === docId && chainSpec() && !CHAIN_OFF && !GRID && !RAW) renderPanel();
}

function chainEdges(rv) {
  const F = rv.edge;
  return (DATA.records || []).filter((r) => (r.field_values || {})[F.from] && (r.field_values || {})[F.to]).map((r) => ({
    rid: r.id, from: String(r.field_values[F.from]).trim(), to: String(r.field_values[F.to]).trim(), sign: r.field_values[F.sign] || "0" }));
}

function renderChainPanel(panel, rv) {
  const pr = docProgress();
  const i = DOCS.findIndex((d) => d.document_id === DOCID);
  const pm = DATA.paper_metadata || DATA.paper || {};
  const title = pm.title || (DOCS[i] || {}).title || (DOCS[i] || {}).filename || "";
  const head = document.createElement("div"); head.className = "chain-head";
  head.innerHTML = `<button type="button" class="chain-nav" data-dir="-1" title="previous paper (←)"${DOCS.length > 1 ? "" : " disabled"}>‹</button>`
    + `<h3 class="chain-title" title="${esc(title)}">${esc(title)}</h3>`
    + `<span class="muted chain-prog">${pr.reviewed} / ${pr.total} reviewed${DOCS.length > 1 ? ` · paper ${i + 1} of ${DOCS.length}` : ""}</span>`
    + `<button type="button" class="chain-nav" data-dir="1" title="next paper (→)"${DOCS.length > 1 ? "" : " disabled"}>›</button>`;
  head.querySelectorAll(".chain-nav").forEach((b) => (b.onclick = () => { const n = DOCS.length; selectDoc(DOCS[(i + (+b.dataset.dir) + n) % n].document_id); }));
  panel.appendChild(head);
  renderChainDag(panel, rv);
  const recs = (DATA.records || []).slice().sort((a, b) => a.entry_index - b.entry_index);
  for (const rec of recs) {
    if (CHAIN_EDIT.has(rec.id)) {
      renderRecordCard(panel, rec);
      const t = panel.lastElementChild && panel.lastElementChild.querySelector(".rectitle");
      if (t) {
        const back = document.createElement("button");
        back.className = "histbtn"; back.textContent = "⛓ chain"; back.title = "back to the chain view of this claim";
        back.onclick = () => { CHAIN_EDIT.delete(rec.id); renderPanel(); };
        t.insertBefore(back, t.querySelector(".histbtn"));
      }
    } else renderChainCard(panel, rec, rv);
  }
  if (!recs.length) panel.insertAdjacentHTML("beforeend", `<p class="muted">No claims were extracted from this paper.</p>`);
  setContextEvidence(null);
}

// The paper's claims as a small graph: nodes are the distinct causes and effects, edges the
// claims, ranked left to right; click an edge to focus its cards.
function renderChainDag(panel, rv) {
  const edges = chainEdges(rv);
  const box = document.createElement("div"); box.className = "chain-dag";
  if (!edges.length) { box.innerHTML = `<p class="muted" style="margin:0">The abstract states no directional causal finding.</p>`; panel.appendChild(box); return; }
  const wrap = (t, n) => { const w = String(t).split(/\s+/), L = []; let c = ""; for (const x of w) { if ((c + " " + x).trim().length > n && c) { L.push(c); c = x; } else c = (c + " " + x).trim(); } if (c) L.push(c); if (L.length > 3) { L.length = 3; L[2] = L[2].replace(/.{0,2}$/, "…"); } return L; };
  const conceptOf = (rid, which) => AUX.concepts && AUX.concepts[rid] ? AUX.concepts[rid][`${rv.edge[which]}_concept`] : null;
  const nodes = new Map();
  for (const e of edges) {
    for (const [key, which] of [[e.from, "from"], [e.to, "to"]]) {
      const k = key.toLowerCase();
      if (!nodes.has(k)) nodes.set(k, { id: k, label: key, lines: wrap(key, 24), concept: conceptOf(e.rid, which) });
      else if (!nodes.get(k).concept) nodes.get(k).concept = conceptOf(e.rid, which);
    }
  }
  const N = [...nodes.values()], E = edges.map((e) => ({ ...e, from: e.from.toLowerCase(), to: e.to.toLowerCase() }));
  const rank = {}; N.forEach((n) => (rank[n.id] = 0));
  for (let it = 0; it < N.length + 1; it++) { let ch = false; for (const e of E) if (e.from !== e.to && rank[e.to] < rank[e.from] + 1 && rank[e.from] + 1 <= 3) { rank[e.to] = rank[e.from] + 1; ch = true; } if (!ch) break; }
  const cols = {}; N.forEach((n) => (cols[rank[n.id]] = cols[rank[n.id]] || []).push(n));
  const ks = Object.keys(cols).map(Number).sort((a, b) => a - b);
  const W = 150, GAPX = 96, GAPY = 12, pos = {};
  ks.forEach((k, ci) => {
    const col = cols[k];
    if (ci > 0) { col.forEach((n) => { const ys = E.filter((e) => e.to === n.id && pos[e.from]).map((e) => pos[e.from].cy); n.bc = ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : 1e6; }); col.sort((a, b) => a.bc - b.bc); }
    else col.sort((a, b) => a.label.localeCompare(b.label));
    let y = 8; col.forEach((n) => { const h = 14 * n.lines.length + 12; pos[n.id] = { x: 8 + ci * (W + GAPX), y, w: W, h, cy: y + h / 2 }; y += h + GAPY; }); cols[k].H = y;
  });
  const H = Math.max(...ks.map((k) => cols[k].H)) + 4, WW = 8 + ks.length * (W + GAPX) - GAPX + 16;
  ks.forEach((k) => { const off = (H - cols[k].H) / 2; cols[k].forEach((n) => { pos[n.id].y += off; pos[n.id].cy += off; }); });
  const pair = {}; E.forEach((e) => (pair[`${e.from}>${e.to}`] = pair[`${e.from}>${e.to}`] || []).push(e));
  const outN = {}, inN = {}; E.forEach((e) => { outN[e.from] = (outN[e.from] || 0) + 1; inN[e.to] = (inN[e.to] || 0) + 1; }); const slot = {};
  const bez = (t, a, b, c, d) => { const u = 1 - t; return u * u * u * a + 3 * u * u * t * b + 3 * u * t * t * c + t * t * t * d; };
  let svg = `<svg class="chain-svg" viewBox="0 0 ${WW} ${H}" width="${WW}" height="${H}"><defs>`
    + Object.entries(SIGN_CLASS).map(([, cls]) => `<marker id="ah-${cls}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="${cls}" d="M0,0 L10,5 L0,10 z"/></marker>`).join("") + `</defs>`;
  for (const e of E) {
    const a = pos[e.from], b = pos[e.to]; if (!a || !b) continue;
    const cls = SIGN_CLASS[e.sign] || "cs-zero", sib = pair[`${e.from}>${e.to}`], off = (sib.indexOf(e) - (sib.length - 1) / 2) * 16;
    let d, mx, my;
    if (rank[e.to] > rank[e.from]) {
      const x1 = a.x + a.w, y1 = a.cy + off * .4, x2 = b.x - 2, y2 = b.cy + off * .4, c = (x2 - x1) * .5;
      d = `M${x1},${y1} C${x1 + c},${y1 + off} ${x2 - c},${y2 + off} ${x2},${y2}`;
      const nearTarget = (outN[e.from] || 0) >= (inN[e.to] || 0), sk = nearTarget ? `t:${e.to}` : `s:${e.from}`, k = slot[sk] = (slot[sk] || 0) + 1;
      const t = nearTarget ? Math.max(.5, .96 - .2 * k) : Math.min(.5, .04 + .2 * k);
      mx = bez(t, x1, x1 + c, x2 - c, x2); my = bez(t, y1, y1 + off, y2 + off, y2);
    } else {
      const x1 = a.x + a.w, y1 = a.cy, x2 = b.x + b.w + 2, y2 = b.cy + 6, r = 54 + Math.abs(off);
      d = `M${x1},${y1} C${x1 + r},${y1} ${x2 + r},${y2} ${x2},${y2}`; mx = Math.max(x1, x2) + r * .75; my = (y1 + y2) / 2;
    }
    svg += `<g class="chain-e" data-rid="${esc(e.rid)}" data-edge="${esc(`${e.from}|${e.to}|${e.sign}`)}"><title>click to focus this claim</title>`
      + `<path class="${cls} chain-line" d="${d}" marker-end="url(#ah-${cls})"/><path class="chain-hit" d="${d}"/>`
      + `<circle class="${cls} chain-dot" cx="${mx}" cy="${my}" r="8"/><text class="${cls} chain-sign" x="${mx}" y="${my + 4}" text-anchor="middle">${SIGN_GLYPH[e.sign] || "0"}</text></g>`;
  }
  for (const n of N) {
    const q = pos[n.id];
    svg += `<g class="chain-n${n.concept ? "" : " unplaced"}"><title>${esc(n.concept || "not placed in a vocabulary")}</title><rect x="${q.x}" y="${q.y}" width="${q.w}" height="${q.h}" rx="6"/>`
      + n.lines.map((ln, k) => `<text x="${q.x + q.w / 2}" y="${q.y + 16 + k * 14}" text-anchor="middle">${esc(ln)}</text>`).join("") + `</g>`;
  }
  svg += `</svg>`;
  box.innerHTML = `<div class="chain-dagscroll">${svg}</div><div class="chain-legend">`
    + Object.entries(rv.edge.signs || {}).map(([sg, word]) => `<span><i class="${SIGN_CLASS[sg] || "cs-zero"}"></i>${esc(word)}</span>`).join("") + `</div>`;
  box.querySelectorAll(".chain-e").forEach((g) => (g.onclick = (ev) => { ev.stopPropagation(); chainFocus(g.dataset.edge, true); }));
  box.querySelector("svg").onclick = () => chainFocus(null);
  panel.appendChild(box);
}
let CHAIN_FOCUS = null;
function chainFocus(edge, scroll) {
  CHAIN_FOCUS = edge;
  document.querySelectorAll(".chain-e").forEach((g) => { g.classList.toggle("on", edge !== null && g.dataset.edge === edge); g.classList.toggle("dim", edge !== null && g.dataset.edge !== edge); });
  let first = null;
  document.querySelectorAll(".chain-card").forEach((c) => { const on = edge === null || c.dataset.edge === edge; c.classList.toggle("dim", !on); if (on && edge !== null && !first) first = c; });
  if (scroll && first) first.scrollIntoView({ block: "start", behavior: "smooth" });
}
function chainStep(dir) {
  const cards = [...document.querySelectorAll(".chain-card:not(.dim)")]; if (!cards.length) return;
  const cur = cards.findIndex((c) => c.classList.contains("cur"));
  const next = cards[Math.max(0, Math.min(cards.length - 1, cur + dir))];
  document.querySelectorAll(".chain-card").forEach((c) => c.classList.remove("cur"));
  next.classList.add("cur"); next.scrollIntoView({ block: "center", behavior: "smooth" });
}

// a quote as the card shows it: verbatim, with the page it was found on as a jump into the PDF
function chainQuote(rec, field, text) {
  const hit = text ? evidenceFor(EV, VM, rec.id, field) : null;
  const ids = hit && hit.kind === "exact" ? hit.ids : null;
  const page = ids ? DATA.evidence[ids[0]].page : null;
  return `<div class="chain-q">“${esc(text)}”` + (ids ? ` <button type="button" class="ev-cite" data-eids="${ids.join(",")}" data-page="${page || 1}" title="${esc(DATA.evidence[ids[0]].snippet || "")}">p. ${page || "?"}</button>` : ` <span class="chain-pg muted">not located in the PDF</span>`) + `</div>`;
}

function renderChainCard(panel, rec, rv) {
  const fv = rec.field_values || {}, F = rv.edge, R = rv.results || null;
  const cause = fv[F.from], effect = fv[F.to], sign = fv[F.sign];
  const isEdge = !!(cause && effect);
  const edgeKey = isEdge ? `${String(cause).trim().toLowerCase()}|${String(effect).trim().toLowerCase()}|${sign || "0"}` : "";
  const concepts = AUX.concepts && AUX.concepts[rec.id] ? AUX.concepts[rec.id] : {};
  const cls = SIGN_CLASS[sign] || "cs-zero";
  const idField = VM.entries.id_field;
  const typeField = rv.qualifies && rv.qualifies.type;
  const kind = typeField ? fv[typeField] : null;
  const card = document.createElement("article");
  card.className = "record chain-card"; card.dataset.rid = rec.id; if (edgeKey) card.dataset.edge = edgeKey;
  const K = rv.constructs || {};
  const node = (text, concept, construct) => `<span class="chain-node">${esc(text)}`
    + (concept ? `<small>${esc(concept)}</small>` : construct ? `<small class="chain-construct" title="the construct this variable indicates">↳ ${esc(construct)}</small>` : "") + `</span>`;
  const edge = isEdge
    ? `<div class="chain-edge">${node(cause, concepts[`${F.from}_concept`], K.from ? fv[K.from] : null)}<span class="chain-arrow ${cls}"><span class="ln"></span><span class="sg ${cls}">${SIGN_GLYPH[sign] || "0"}</span><span class="ln"></span><span class="hd"></span></span>${node(effect, concepts[`${F.to}_concept`], K.to ? fv[K.to] : null)}</div>`
    : `<div class="chain-stmt">${esc(fv[rv.statement] || entryTitle(VM, rec, rec.entry_index))}${kind ? ` <span class="tag">${esc(kind)}</span>` : ""}</div>`;
  const scope = (rv.scope || []).map((n) => fv[n]).filter((v) => v !== null && v !== undefined && v !== "");
  // the quotes
  let dl = "";
  for (const q of rv.quotes || []) {
    const text = fv[q.field];
    dl += `<dt>${esc(q.label)}</dt><dd>${text ? chainQuote(rec, q.field, text) : `<span class="chain-none muted">${q.field === (rv.quotes[0] || {}).field ? "not stated in the abstract" : "no sentence recorded"}</span>`}</dd>`;
  }
  // the results that carry the claim
  if (R) {
    const rows = Array.isArray(fv[R.child]) ? fv[R.child] : [];
    let body = "";
    rows.forEach((row, j) => {
      const path = `${R.child}[${j}]`;
      const hit = evidenceFor(EV, VM, rec.id, `${path}.${R.value}`) || evidenceFor(EV, VM, rec.id, path);
      const ids = hit && hit.kind !== "entry" ? hit.ids : null;
      const page = ids ? DATA.evidence[ids[0]].page : null;
      const verdict = AUX.cross ? AUX.cross[`${rec.id}|${path}`] : null;
      const chk = !verdict ? (AUX.cross ? "" : `<span class="chain-chk unchecked">not cross-checked</span>`)
        : verdict.status === "exact" ? `<span class="chain-chk agrees" title="${esc(verdict.detail || "")}">✓ matches the table extraction</span>`
        : verdict.status === "mismatch" ? `<span class="chain-chk mismatch" title="${esc(verdict.detail || "")}">✗ DISAGREES with the table extraction</span>`
        : verdict.status === "figure" ? `<span class="chain-chk unchecked">a figure · nothing to transcribe against</span>`
        : `<span class="chain-chk unchecked" title="${esc(verdict.detail || "")}">${esc(verdict.status === "unlinked" ? "no matching table column" : verdict.status.replace("_", " "))}</span>`;
      const ex = (R.exhibit || []).map((n) => row[n]).filter(Boolean).join(" ");
      const val = row[R.value], se = R.se ? row[R.se] : null;
      const rel = (which) => { const f = which === "cause" ? R.cause_relation : R.effect_relation; const v = f ? row[f] : null; if (!v) return ""; return v === "direct" ? `<span class="tag">direct measure</span>` : `<span class="tag warn">${esc(v === "assignment" ? "assignment indicator (stands in for the cause)" : v)}</span>`; };
      const role = R.role && row[R.role] && row[R.role] !== "main" ? ` <span class="tag">${esc(row[R.role])}</span>` : "";
      const stratum = [R.subgroup ? row[R.subgroup] : null, R.horizon ? row[R.horizon] : null].filter(Boolean)
        .map((x) => `<span class="tag chain-stratum">${esc(x)}</span>`).join("")
        + (R.value_from && row[R.value_from] === "text" ? `<span class="tag warn" title="the exhibit does not print this number; the paper states it in prose">number from the text</span>` : "")
        + (R.value_from && row[R.value_from] === "absent" ? `<span class="tag warn">no number reported</span>` : "");
      const signOpp = R.sign_consistent && row[R.sign_consistent] === false ? ` <span class="chain-chk mismatch">sign opposes the claim</span>` : "";
      body += `<div class="chain-res" data-path="${esc(path)}"><div class="chain-line"><b>${esc(ex || "—")}</b>`
        + (ids ? ` <button type="button" class="ev-cite" data-eids="${ids.join(",")}" data-page="${page || 1}" title="${esc(DATA.evidence[ids[0]].snippet || "")}">p. ${page || "?"}</button>` : "")
        + ` <span class="chain-num"><span class="rv-editable rv-num rv-empty" data-path="${esc(path)}.${esc(R.value)}" contenteditable="plaintext-only" title="the point estimate — click to correct it">${val === null || val === undefined ? "" : esc(String(val))}</span>`
        + (R.se ? ` (<span class="rv-editable rv-num rv-empty" data-path="${esc(path)}.${esc(R.se)}" contenteditable="plaintext-only" title="the standard error — click to correct it">${se === null || se === undefined ? "" : esc(String(se))}</span>)` : "")
        + `</span> ${stratum}${chk}${signOpp}${role}<button type="button" class="chain-resdel" data-j="${j}" title="remove this result row from the claim">✕</button></div>`
        + (R.row && row[R.row] ? `<div class="chain-rowlab muted">${esc(row[R.row])}</div>` : "")
        + (R.cause ? `<div class="chain-op"><span class="k">cause</span><span>${esc(row[R.cause] || "—")} ${rel("cause")}</span></div>` : "")
        + (R.effect ? `<div class="chain-op"><span class="k">effect</span><span>${esc(row[R.effect] || "—")} ${rel("effect")}</span></div>` : "")
        + (R.why && row[R.why] ? `<details class="chain-why"><summary class="muted">why this result</summary><div class="muted">${esc(row[R.why])}</div></details>` : "")
        + `</div>`;
    });
    if (!rows.length) body = `<span class="chain-none muted">${isEdge ? (fv[rv.notes] ? `no extracted result carries this claim — ${esc(fv[rv.notes])}` : "no result mapped") : "—"}</span>`;
    body += `<div><button type="button" class="chain-addres" title="add a table result to this claim">＋ result</button></div>`;
    dl += `<dt>${esc(R.label || "Results")}</dt><dd>${body}</dd>`;
  }
  // comparisons that qualify this claim
  if (rv.qualifies && idField) {
    const Q = rv.qualifies;
    const mine = String(fv[idField] || "");
    const quals = (DATA.records || []).filter((r) => r.id !== rec.id && Array.isArray((r.field_values || {})[Q.field]) && r.field_values[Q.field].map(String).includes(mine));
    if (quals.length) dl += `<dt>Qualified by</dt><dd>${quals.map((r) => `<div class="chain-lineq">${esc(r.field_values[rv.statement] || entryTitle(VM, r, r.entry_index))}${Q.moderator && r.field_values[Q.moderator] ? ` <span class="muted">(${esc(r.field_values[Q.moderator])})</span>` : ""}</div>`).join("")}</dd>`;
  }
  if (rv.notes && fv[rv.notes]) dl += `<dt>Note</dt><dd class="chain-modelnote">${esc(fv[rv.notes])}</dd>`;
  const support = rv.support ? fv[rv.support] : null;
  const label = (rv.support_labels || {})[support] || (rv.refined && fv[rv.refined] ? "abstract announces it · the introduction spells it out" : "");
  const worst = worstLevel(VM, { ...(rec.confidence || {}), ...Object.assign({}, ...Object.values(rec.child_confidence || {})) });
  card.innerHTML = `<div class="rectitle"><code class="chain-id">${esc(idField ? fv[idField] || "" : entryTitle(VM, rec, rec.entry_index))}</code>`
    + `<span class="status ${rec.verification_status}">${rec.verification_status}</span>` + (worst ? renderConfDot(worst, VM.levels, `lowest confidence: ${worst}`) : "")
    + `<span style="margin-left:auto"></span><button class="histbtn chain-editbtn" title="every field of this claim, editable — corrections are logged">▤ edit</button><button class="histbtn" title="change history">↻ history</button><button class="recdel" title="delete this ${esc(VM.entries.label.toLowerCase())}">🗑</button></div>`
    + edge + (scope.length ? `<div class="chain-scope muted">${scope.map(esc).join(" · ")}</div>` : "")
    + `<dl class="chain-dl">${dl}</dl>`
    + `<div class="chain-foot"><button class="vbtn ok${rec.verification_status === "verified" ? " on" : ""}" data-status="verified">OK</button>`
    + `<button class="vbtn flag${rec.verification_status === "flagged" ? " on" : ""}" data-status="flagged">Flag</button>`
    + `<button class="vbtn notebtn" title="write a note on this claim — it is stored with your name and the time, and does not verify or flag anything">✎ Note</button>`
    + chainReviewNote(rec)
    + (label ? `<span class="tag chain-sup${support === "weak" ? " warn" : ""}">${esc(label)}</span>` : "") + `</div><div class="histbody" hidden></div>`;
  // wiring: history, delete, verify/flag, the quotes' jumps, the edge focus
  card.querySelector(".chain-editbtn").onclick = () => { CHAIN_EDIT.add(rec.id); renderPanel(); };
  // a corrected number is a correction, not a verification: same path as the field view
  wireControls(card, (p, v) => saveFieldEdit(rec, card, p, v, curVal(rec, p)), true);
  card.querySelectorAll(".chain-resdel").forEach((b) => (b.onclick = async () => {
    const j = Number(b.dataset.j);
    const rows2 = (rec.field_values || {})[R.child] || [];
    const row = rows2[j] || {};
    const label = [(R.exhibit || []).map((n) => row[n]).filter(Boolean).join(" "), row[R.value]].filter((x) => x !== null && x !== undefined && x !== "").join(" · ") || "this row";
    if (!confirm(`Remove ${label} from this claim? The change is logged and the claim stays.`)) return;
    rows2.splice(j, 1);
    try {
      await saveWholeRecord(rec, [{ field_path: `${R.child}[${j}]`, original_value: label, final_value: "(row removed)" }]);
      maybeOfferRepublish(); renderPanel();
    } catch (e) { rows2.splice(j, 0, row); alert("remove failed: " + e.message); }
  }));
  const addres = card.querySelector(".chain-addres");
  if (addres) addres.onclick = async () => {
    const ch = (VM.fieldIndex.get(R.child) || {}).child;
    const blank = Object.fromEntries(((ch && ch.fields) || []).map((f) => [f.name, null]));
    if (!rec.field_values) rec.field_values = {};
    const rows = (rec.field_values[R.child] = [...(rec.field_values[R.child] || []), blank]);
    try {
      await saveWholeRecord(rec, [{ field_path: `${R.child}[${rows.length - 1}]`, original_value: null, final_value: "(row added)" }]);
      maybeOfferRepublish();
      CHAIN_EDIT.add(rec.id);                    // straight into the field view to fill it in
      renderPanel();
    } catch (e) { rows.pop(); alert("add failed: " + e.message); }
  };
  card.querySelector(".histbtn:not(.chain-editbtn)").onclick = () => toggleHistory(card, rec);
  card.querySelector(".recdel").onclick = () => doDeleteRecord(rec);
  card.querySelectorAll(".vbtn[data-status]").forEach((b) => (b.onclick = async () => {
    await sendVerify(card, rec, b.dataset.status, chainNoteText(card));
    card.querySelectorAll(".vbtn").forEach((x) => x.classList.toggle("on", x.dataset.status === rec.verification_status));
    card.querySelector(".chain-review").hidden = rec.verification_status !== "flagged" && !rec.note;
  }));
  wireChainNote(card, rec);
  card.querySelector(".notebtn").onclick = () => {
    const box = card.querySelector(".chain-review");
    box.hidden = false;
    box.querySelector(".chain-notein").focus();
  };
  card.querySelectorAll(".ev-cite[data-eids]").forEach((b) => {
    const ids = b.dataset.eids.split(",").map(Number);
    b.onclick = (e) => { e.stopPropagation(); jumpToEvidence(+b.dataset.page, ids); };
    b.onmouseenter = () => showEvidence(ids); b.onmouseleave = () => hideEvidence(ids);
  });
  const ed = card.querySelector(".chain-edge"); if (ed) ed.onclick = () => chainFocus(CHAIN_FOCUS === edgeKey ? null : edgeKey, false);
  card.addEventListener("click", () => { document.querySelectorAll(".chain-card").forEach((c) => c.classList.remove("cur")); card.classList.add("cur"); });
  panel.appendChild(card);
}

// The reviewer's verdict in the chain layout: OK / Flag write a verification event on the entry
// (who, when, status — the same record the audit report and the credibility badge read). A flag
// also carries WHY: a reason and a free note, stored as the event's notes and shown back here.
const CHAIN_REASONS = [["", "what is wrong…"], ["claim", "the claim itself (cause / effect / sign)"],
                       ["split", "should be split or merged"], ["evidence", "the evidence does not support it"],
                       ["table", "wrong table line / result"], ["measurement", "the operationalisation"], ["other", "other"]];
function chainSplitNote(text) {
  for (const [, label] of CHAIN_REASONS) {
    if (label && text && (text === label || text.startsWith(`${label} — `))) return [label, text.slice(label.length + 3)];
  }
  return ["", text || ""];
}
function chainReviewNote(rec) {
  const [reason, text] = chainSplitNote((rec.note || {}).text);
  const open = rec.verification_status === "flagged" || !!(rec.note || {}).text;
  const meta = (rec.note || {}).by
    ? `<span class="chain-noteby muted" title="who wrote the last note, and when">${esc(rec.note.by)}${rec.note.at ? ` · ${esc(new Date(rec.note.at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }))}` : ""}</span>` : "";
  return `<span class="chain-review"${open ? "" : " hidden"}>`
    + `<select class="chain-reason">${CHAIN_REASONS.map(([v, t]) => `<option value="${esc(t)}"${t === reason ? " selected" : ""}${v ? "" : ' data-empty="1"'}>${esc(t)}</option>`).join("")}</select>`
    + `<input class="chain-notein" placeholder="note — saved as you leave the field, with your name and the time" value="${esc(text)}"/>${meta}</span>`;
}
function chainNoteText(card) {
  const box = card.querySelector(".chain-review"); if (!box) return undefined;
  const sel = box.querySelector(".chain-reason"), inp = box.querySelector(".chain-notein");
  const reason = sel.selectedOptions[0] && !sel.selectedOptions[0].dataset.empty ? sel.value : "";
  const text = inp.value.trim();
  return reason && text ? `${reason} — ${text}` : reason || text;
}
function wireChainNote(card, rec) {
  const box = card.querySelector(".chain-review"); if (!box) return;
  // A note used to force the record to "flagged" when it was unverified — so there was no way
  // to write anything down WITHOUT passing judgement. A note is now its own event ("noted"):
  // stored with name and time, shown in the history, and the verdict stays whatever the
  // reviewer last decided with the buttons.
  const save = async () => {
    const text = chainNoteText(card);
    if (!text && !(rec.note || {}).text) return;              // nothing to record
    try {
      await api.verify(rec.id, { status: "noted", notes: text || "" });
      rec.note = text ? { text, status: "noted", by: "you", at: new Date().toISOString() } : null;
      const meta = box.querySelector(".chain-noteby");
      if (meta) meta.textContent = "you · just now"; else if (text) {
        const sp = document.createElement("span"); sp.className = "chain-noteby muted"; sp.textContent = "you · just now";
        box.appendChild(sp);
      }
      maybeOfferRepublish();
    } catch (e) { alert("note failed: " + e.message); }
  };
  box.querySelector(".chain-reason").onchange = save;
  box.querySelector(".chain-notein").onchange = save;
}

// ── the evidence layout: the reader's items first, the cells each bears on beneath ────────────
// Declared by display.review (layout "evidence"): `items` names the entry TABLE field that holds
// the reader's numbered items — one row each: n, page, kind (passage | table), text (the passage,
// or a table as markdown), note, bears_on (the fields it supports) — and every evidence row whose
// source starts "item N (kind)" belongs to item N. Drawn per paper: a status strip per entry
// (verify / flag, history), then the items in order — the quote or the table, a jump into the
// PDF, the study and entry cells the item bears on, the arm and result fields it names — then
// everything no item bears on, so nothing is hidden. The cells are the generic card's cells
// (same paths, same save and verification), arranged by evidence.
function evidenceSpec() {
  const rv = VM && !VM.legacy && VM.spec && VM.spec.display ? VM.spec.display.review : null;
  return rv && rv.layout === "evidence" ? rv : null;
}
function stagesSpec() {
  const rv = VM && !VM.legacy && VM.spec && VM.spec.display ? VM.spec.display.review : null;
  return rv && rv.layout === "stages" ? rv : null;
}
function reviewSpec() { return chainSpec() || evidenceSpec() || stagesSpec(); }
function reviewToggleLabel() { return chainSpec() ? "⛓ Chain" : stagesSpec() ? "☰ Stages" : "❝ Evidence"; }
function reviewToggleTitle() {
  return chainSpec() ? "the claims as cause → effect chains with their quotes and results"
    : stagesSpec() ? "the paper coded stage by stage, the evidence of each stage first"
    : "the evidence items, each with the fields it bears on";
}

// The head of a review layout: previous / next paper, the title, the review progress, and the
// model's ratings of the paper-level groups (design, sample …).
function renderReviewHead(panel) {
  const pr = docProgress();
  const i = DOCS.findIndex((d) => d.document_id === DOCID);
  const pm = DATA.paper_metadata || {};
  const title = pm.title || (DATA.paper || {}).title || (DOCS[i] || {}).title || (DOCS[i] || {}).filename || "";
  const head = document.createElement("div"); head.className = "chain-head";
  head.innerHTML = `<button type="button" class="chain-nav" data-dir="-1" title="previous paper"${DOCS.length > 1 ? "" : " disabled"}>‹</button>`
    + `<h3 class="chain-title" title="${esc(title)}">${esc(title)}</h3>`
    + `<span class="muted chain-prog">${pr.reviewed} / ${pr.total} reviewed${DOCS.length > 1 ? ` · paper ${i + 1} of ${DOCS.length}` : ""}</span>`
    + `<button type="button" class="chain-nav" data-dir="1" title="next paper"${DOCS.length > 1 ? "" : " disabled"}>›</button>`;
  head.querySelectorAll(".chain-nav").forEach((b) => (b.onclick = () => { const n = DOCS.length; selectDoc(DOCS[(i + (+b.dataset.dir) + n) % n].document_id); }));
  panel.appendChild(head);
  const pgroups = Object.values(VM.groups).filter((g) => g.scope === "paper" && (DATA.paper_confidence || {})[g.id]);
  if (pgroups.length) {
    const gh = document.createElement("div"); gh.className = "group-head evi-pgroups";
    gh.innerHTML = pgroups.map((g) => renderConfBadge(g.id, g, DATA.paper_confidence[g.id], VM.levels)).join("");
    panel.appendChild(gh); wireConfBadges(gh);
  }
}

const EVI_SRC = /^item\s+(\d+)\s*\((\w+)[^)]*\)\s*(?:→\s*(.*))?$/;
// The evidence rows grouped by item, in item order: {n, kind, page, ids, snippet, paper: [field],
// entry: Map(record id → [field]), lower: [{level, name, recId}] (arm / result fields the item
// names — those have no cell on the entry, they live on the sub-entry tabs)}.
function evidenceGroups() {
  const groups = new Map();
  const key = VM.entries.key;
  const byIndex = new Map((DATA.records || []).map((r) => [r.entry_index, r]));
  (DATA.evidence || []).forEach((ev, i) => {
    const m = EVI_SRC.exec(String(ev.source || "").trim());
    if (!m) return;
    const n = +m[1];
    if (!groups.has(n)) groups.set(n, { n, kind: m[2], page: ev.page || null, ids: [], snippet: ev.snippet || "", paper: [], entry: new Map(), lower: [] });
    const g = groups.get(n);
    g.ids.push(i);
    if (!g.page && ev.page) g.page = ev.page;
    const fp = String(ev.field_path || "");
    const pm = /^paper_metadata\.([A-Za-z_][A-Za-z0-9_]*)$/.exec(fp);
    if (pm) { if (!g.paper.includes(pm[1])) g.paper.push(pm[1]); return; }
    const rp = relPath(VM, fp);
    let rec = ev.record_id ? (DATA.records || []).find((r) => r.id === ev.record_id) : null;
    if (!rec && rp && rp.key === key && byIndex.has(rp.entryIndex)) rec = byIndex.get(rp.entryIndex);
    if (!rec && ev.entry_index != null && byIndex.has(ev.entry_index)) rec = byIndex.get(ev.entry_index);
    if (!rec) return;
    const rel = rp ? rp.rel : "";
    if (rel && /^[A-Za-z_][A-Za-z0-9_]*$/.test(rel)) {
      if (!g.entry.has(rec.id)) g.entry.set(rec.id, []);
      if (!g.entry.get(rec.id).includes(rel)) g.entry.get(rec.id).push(rel);
    } else if (rel) {                      // "arms[1].n_arm": that row's cell, drawn under the item
      const p = parseRel(VM, rel);
      if (p.childKey && p.field && !g.lower.some((x) => x.name === p.field && x.recId === rec.id && x.childIndex === p.childIndex))
        g.lower.push({ level: p.childKey, name: p.field, recId: rec.id, childIndex: p.childIndex });
    }
    for (const part of (m[3] || "").split(/,\s*/)) {
      const lm = /^(\w+):\s*([A-Za-z_][A-Za-z0-9_]*)$/.exec(part.trim());
      if (lm && !g.lower.some((x) => x.level === lm[1] && x.name === lm[2] && x.recId === rec.id)) g.lower.push({ level: lm[1], name: lm[2], recId: rec.id });
    }
  });
  return [...groups.values()].sort((a, b) => a.n - b.n);
}

// The items table's rows as a map n → item; a table item's text is its markdown, kept as lines.
function itemsFromRows(rows) {
  const out = new Map();
  for (const r of Array.isArray(rows) ? rows : []) {
    if (!r || r.n === null || r.n === undefined || r.n === "") continue;
    const n = +r.n; if (!Number.isFinite(n)) continue;
    const kind = r.kind === "table" ? "table" : "passage";
    const text = r.text === null || r.text === undefined ? "" : String(r.text);
    out.set(n, { n, page: r.page === null || r.page === undefined ? "" : String(r.page), kind, note: r.note ? String(r.note) : "",
                 text: kind === "table" ? "" : text.trim().replace(/^[“"]/, "").replace(/[”"]$/, ""),
                 lines: kind === "table" ? text.split("\n") : [],
                 bearsOn: Array.isArray(r.bears_on) ? r.bears_on.map(String) : [] });
  }
  return out;
}

// A table item's markdown (caption, header, rows) as an HTML table.
function eviTableHtml(lines, fallbackCaption) {
  let cap = "", headed = false; const rows = [];
  for (const ln of lines) {
    const t = ln.trim();
    if (/^\*\*.+\*\*$/.test(t)) cap = t.slice(2, -2);
    else if (/^\|.*\|$/.test(t)) {
      if (/^\|[\s:|-]+\|$/.test(t)) { headed = rows.length === 1; continue; }
      rows.push(t.slice(1, -1).split("|").map((c) => c.trim()));
    }
  }
  const capHtml = cap || fallbackCaption ? `<div class="evi-cap">${esc(cap || fallbackCaption)}</div>` : "";
  if (!rows.length) return capHtml || `<span class="muted">table body not available</span>`;
  const head = headed ? rows.shift() : null;
  return capHtml + `<div class="evi-tablewrap"><table class="evi-table">`
    + (head ? `<thead><tr>${head.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>` : "")
    + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function eviCite(g) {
  const page = g.page || (g.ids.length ? DATA.evidence[g.ids[0]].page : null);
  if (!g.ids.length) return `<span class="ev-cite ev-none" title="not located in the PDF">p. ${esc(String(page || "?"))}</span>`;
  return `<button type="button" class="ev-cite" data-eids="${g.ids.join(",")}" data-page="${page || 1}" title="${esc(g.snippet)}">p. ${esc(String(page || "?"))}</button>`;
}

// the arm / result fields an item names, by the sub-entry they belong to
function eviChildren(lower) {
  const out = new Map();
  for (const x of lower) {
    const ch = VM.entries.children.find((c) => (c.fields || []).some((f) => f.name === x.name));
    const k = ch ? ch.key : x.level;
    if (!out.has(k)) out.set(k, { child: ch || null, labels: [] });
    const f = ch ? ch.fields.find((y) => y.name === x.name) : null;
    const label = f ? f.label || formatKey(x.name) : x.name;
    if (!out.get(k).labels.includes(label)) out.get(k).labels.push(label);
  }
  return out;
}

function eviPaperBlock(names) {
  const defs = VM.paper.fields.filter((f) => names.includes(f.name));
  if (!defs.length) return "";
  return `<div class="evi-paper paper-fields"><div class="evi-sub">Study</div><div class="rv-root">${renderFields(defs, DATA.paper_metadata || {}, "", { editable: true })}</div></div>`;
}

function eviEntryBlock(rec, names, kids) {
  const defs = VM.entries.fields.filter((f) => names.includes(f.name));
  if (!defs.length && !kids.size) return "";
  let html = `<div class="evi-entry" data-rid="${esc(rec.id)}"><div class="evi-sub">${esc(entryTitle(VM, rec, rec.entry_index))}</div>`;
  if (defs.length) html += `<div class="rv-root">${renderFields(defs, rec.field_values || {}, "", { editable: true })}</div>`;
  for (const [k, { child, labels }] of kids) {
    const label = child ? child.label || k : k;
    const rows = child && Array.isArray((rec.field_values || {})[k]) ? rec.field_values[k].length : null;
    html += `<div class="evi-lower muted">` + (labels.length ? `bears on <b>${esc(label)}</b>: ${labels.map(esc).join(", ")}` : `<b>${esc(label)}</b>`)
      + (rows !== null ? ` <span class="tag">${rows} so far</span>` : "")
      + (child ? ` <button type="button" class="evi-go" data-child="${esc(k)}" title="${esc(label)} are added and edited on the fields view">open ${esc(label)} ↗</button>` : "")
      + `</div>`;
  }
  return html + `</div>`;
}

// The reader's items: the rows of the items table on each record (entry level) or on the paper.
// n → item; owner n → the record, or null for a paper-level item (it belongs to no entry).
function reviewItems(rv) {
  const parsed = new Map(), owner = new Map();
  const recs = (DATA.records || []).slice().sort((a, b) => a.entry_index - b.entry_index);
  for (const rec of recs) for (const [n, it] of itemsFromRows((rec.field_values || {})[rv.items])) if (!parsed.has(n)) { parsed.set(n, it); owner.set(n, rec); }
  for (const [n, it] of itemsFromRows((DATA.paper_metadata || {})[rv.items])) if (!parsed.has(n)) { parsed.set(n, it); owner.set(n, null); }
  return { parsed, owner, recs };
}

// The evidence groups and the items table joined: an item the table lists but no evidence row
// cites (its quote was not found, say) still shows, and the row's own bears_on places the item's
// cells even where no evidence row was located. A paper-level item bears on every record's entry
// and sub-entry fields. g.names holds everything the item bears on, whatever the level.
function mergedItems(rv) {
  const { parsed, owner, recs } = reviewItems(rv);
  const groups = evidenceGroups();
  for (const [n, it] of parsed) if (!groups.some((g) => g.n === n)) groups.push({ n, kind: it.kind, page: +it.page || null, ids: [], snippet: it.text || "", paper: [], entry: new Map(), lower: [] });
  const pnames = new Set(VM.paper.fields.map((f) => f.name)), enames = new Set(VM.entries.fields.map((f) => f.name));
  for (const g of groups) {
    g.names = new Set([...g.paper, ...[...g.entry.values()].flat(), ...g.lower.map((x) => x.name)]);
    const it = parsed.get(g.n); if (!it) continue;
    const rec = owner.get(g.n);
    const targets = rec ? [rec] : recs;
    for (const name of it.bearsOn) {
      g.names.add(name);
      if (pnames.has(name)) { if (!g.paper.includes(name)) g.paper.push(name); continue; }
      for (const r of targets) {
        if (enames.has(name)) { if (!g.entry.has(r.id)) g.entry.set(r.id, []); if (!g.entry.get(r.id).includes(name)) g.entry.get(r.id).push(name); }
        else if (!g.lower.some((x) => x.name === name && x.recId === r.id && x.childIndex == null)) g.lower.push({ level: "", name, recId: r.id, childIndex: null });
      }
    }
  }
  groups.sort((a, b) => a.n - b.n);
  return { groups, parsed, recs };
}

// One item: its head (number, kind, page jump, note), its body (the quote, or the table), and
// the cells it bears on — the default block, or the `cells` html a layout passes (null: none).
function renderEviItem(g, parsed, recs, cells, { bare = false } = {}) {
  const it = parsed || {};
  const kind = g.kind || it.kind || "passage";
  const sec = document.createElement("section");
  sec.className = `evi-item evi-${esc(kind)}${bare ? " evi-bare" : ""}`; sec.dataset.item = String(g.n);
  const body = kind === "table"
    ? `<div class="evi-body evi-tbl">${eviTableHtml(it.lines || [], g.snippet)}</div>`
    : `<div class="evi-body">“${esc(it.text || g.snippet)}”</div>`;
  if (cells === undefined) {
    cells = g.paper.length ? eviPaperBlock(g.paper) : "";
    for (const rec of recs) cells += eviEntryBlock(rec, g.entry.get(rec.id) || [], eviChildren(g.lower.filter((x) => x.recId === rec.id)));
  }
  // bare: the page tag and the quote only (the staged layout's list); else number, kind and note too
  const head = bare ? `<div class="evi-head">${eviCite(g)}</div>`
    : `<div class="evi-head"><span class="evi-id">item ${g.n}</span><span class="tag">${esc(kind)}</span>${eviCite(g)}`
      + (it.note ? `<span class="evi-note muted">${esc(it.note)}</span>` : "") + `</div>`;
  sec.innerHTML = head + body
    + (cells === null ? "" : cells ? `<div class="evi-cells">${cells}</div>` : `<div class="evi-cells muted evi-nothing">bears on no field in particular</div>`);
  return sec;
}

// What no item bears on: the remaining study fields (all of them, or the preset's
// paper_fields), each entry's remaining fields, and its sub-entries (arms, results …).
function renderEviRest(rv, groups, recs) {
  const seenPaper = new Set(groups.flatMap((g) => g.paper));
  const allowed = Array.isArray(rv.paper_fields) ? new Set(rv.paper_fields) : null;
  const paperRest = VM.paper.fields.filter((f) => f.name !== rv.items && !seenPaper.has(f.name) && (!allowed || allowed.has(f.name))).map((f) => f.name);
  let cells = paperRest.length ? eviPaperBlock(paperRest) : "";
  for (const rec of recs) {
    const seen = new Set(groups.flatMap((g) => g.entry.get(rec.id) || []));
    const rest = VM.entries.fields.filter((f) => f.name !== rv.items && !seen.has(f.name)).map((f) => f.name);
    const kids = new Map(VM.entries.children.map((c) => [c.key, { child: c, labels: [] }]));
    cells += eviEntryBlock(rec, rest, kids);
  }
  const sec = document.createElement("section");
  sec.className = "evi-item evi-rest";
  sec.innerHTML = `<div class="evi-head"><span class="evi-id">no item bears on these</span><span class="muted" style="font-size:12px">judge them from the PDF, or leave them empty</span></div>`
    + (cells ? `<div class="evi-cells">${cells}</div>` : "");
  return sec;
}

// One strip per entry: its title, review status, the model's ratings, verify / flag, history.
function renderEviStrip(panel, rec) { panel.appendChild(eviStripEl(rec)); }
function eviStripEl(rec) {
  const card = document.createElement("div");
  card.className = "record evi-rec"; card.dataset.rid = rec.id;
  const worst = worstLevel(VM, { ...(rec.confidence || {}), ...Object.assign({}, ...Object.values(rec.child_confidence || {})) });
  const groups = Object.entries(rec.confidence || {}).filter(([g]) => VM.groups[g]);
  card.innerHTML = `<div class="rectitle">${esc(entryTitle(VM, rec, rec.entry_index))}`
    + `<span class="status ${esc(rec.verification_status)}">${esc(rec.verification_status)}</span>`
    + (worst ? renderConfDot(worst, VM.levels, `lowest confidence: ${worst}`) : "")
    + `<button class="histbtn" title="change history">↻ history</button>`
    + `<button class="recdel" title="delete this ${esc(VM.entries.label.toLowerCase())}">🗑</button></div>`
    + `<div class="verify"><button class="vbtn ok" data-status="verified">✓ verify</button>`
    + `<button class="vbtn flag" data-status="flagged">⚑ flag</button></div>`
    + (groups.length ? `<div class="group-head">${groups.map(([g, r]) => renderConfBadge(g, VM.groups[g], r, VM.levels)).join("")}</div>` : "")
    + `<div class="histbody" hidden></div>`;
  card.querySelector(".histbtn").onclick = () => toggleHistory(card, rec);
  card.querySelector(".recdel").onclick = () => doDeleteRecord(rec);
  card.querySelectorAll(".vbtn").forEach((b) => (b.onclick = () => sendVerify(card, rec, b.dataset.status)));
  wireConfBadges(card);
  return card;
}

function renderEvidencePanel(panel, rv) {
  renderReviewHead(panel);
  const { groups, parsed, recs } = mergedItems(rv);
  for (const rec of recs) renderEviStrip(panel, rec);
  for (const g of groups) panel.appendChild(renderEviItem(g, parsed.get(g.n), recs));
  if (!groups.length) panel.insertAdjacentHTML("beforeend", `<p class="muted">No evidence items on this paper.</p>`);
  panel.appendChild(renderEviRest(rv, groups, recs));
  wireEvidencePanel(panel, recs);
  setContextEvidence(null);
}

// A field several items bear on is drawn under each of them: after an edit the twins follow.
function eviSync(scope, path, val) {
  const sel = `#panelbody ${scope} [data-path="${CSS.escape(path)}"]`;
  document.querySelectorAll(sel).forEach((cell) => {
    if (cell.classList.contains("rv-editable")) {
      const txt = val === null || val === undefined ? "" : String(val);
      if (cell.textContent !== txt) cell.textContent = txt;
      cell.dataset.orig = txt; cell.classList.toggle("rv-empty", txt.trim() === "");
    } else if (cell.classList.contains("rv-selwrap")) {
      const s = cell.querySelector("select"), other = cell.querySelector(".rv-other");
      const sv = val === null || val === undefined ? "" : String(val);
      if ([...s.options].some((o) => o.value === sv)) { s.value = sv; if (other) other.hidden = true; }
      else if (other) { s.value = "__other__"; other.hidden = false; other.value = sv; }
    } else if (cell.classList.contains("rv-multi")) {
      multiSetValues(cell, val);
    }
  });
}

function wireEvidencePanel(panel, recs) {
  const byId = new Map(recs.map((r) => [r.id, r]));
  panel.querySelectorAll(".evi-paper").forEach((box) => {
    wireControls(box, (p, v) => { eviSync(".evi-paper", p, v); return savePaperPath(p, v); }, true);
    wirePaperTableRows(box, rerenderKeep);
    linkPaperCells(box);
  });
  panel.querySelectorAll(".evi-entry[data-rid]").forEach((box) => {
    const rec = byId.get(box.dataset.rid); if (!rec) return;
    const scope = `.evi-entry[data-rid="${rec.id}"]`;
    wireControls(box, (p, v) => { const was = curVal(rec, p); eviSync(scope, p, v); return saveFieldEdit(rec, null, p, v, was); }, true);
    linkCells(box, rec);
    box.querySelectorAll(".evi-go").forEach((b) => (b.onclick = () => eviOpenFields(rec, b.dataset.child)));
  });
  panel.querySelectorAll(".evi-head .ev-cite[data-eids]").forEach((b) => {
    const ids = b.dataset.eids.split(",").map(Number);
    b.onclick = (e) => { e.stopPropagation(); jumpToEvidence(+b.dataset.page, ids); };
    b.onmouseenter = () => showEvidence(ids); b.onmouseleave = () => hideEvidence(ids);
  });
}

// ── the staged layout: the paper coded stage by stage, the evidence first, the PDF beside ───────
// Declared by display.review (layout "stages"): `items` names the reader's items table (on the
// entry or on the paper) and `stages` the ordered stages, each naming the fields of ONE level —
// paper fields, entry fields, or one child's fields. The workspace keeps its two columns: the
// stage panel on the left scrolls as one — the stage bar and its instruction, then EVIDENCE (one
// plain list of the items whose bears_on meets the stage's fields, the current one marked, the
// rest folded at the end), then FIELDS (the stage's cells, once: the study cells; every entry as
// a card with the stage's fields and "＋ add"; or each entry's sub-entries with "＋", "✕", "copy
// … from <entry>" and, where the stage declares copy_from_sibling, "same as <the reference row>")
// — and the PDF column on the right follows the cursor: at the scale the coder set, scrolled so
// the current item's highlight starts just under the column's top edge (an item with no located
// rectangle: the top of its page). ← → and the column's "◀ item k of n ▶" move the cursor, which
// stops at the ends and starts on the first item when a stage opens. A stage's auto_id fills the
// id of an added entry or row (first, then next with the smallest unused number). Saves and
// verification are the generic card's; the Fields view is the generic card view.
function stagesOpen() { return !!(stagesSpec() && !CHAIN_OFF && !GRID && !RAW); }

// The item control at the top of the PDF column while a stage is open; gone otherwise.
function setStageControl(on) {
  const pane = $("#pagespane"); if (!pane) return;
  let nav = pane.querySelector(".stg-pane-nav");
  if (on && !nav) {
    nav = document.createElement("div"); nav.className = "stg-pane-nav";
    nav.innerHTML = `<button type="button" data-d="-1" title="previous item (←)">◀</button><span class="stg-pane-k">—</span><button type="button" data-d="1" title="next item (→)">▶</button>`;
    nav.querySelectorAll("button").forEach((b) => (b.onclick = () => stageCursor(STAGE_CUR + Number(b.dataset.d))));
    pane.appendChild(nav);
  } else if (!on && nav) {
    nav.remove(); STAGE_SHOWN = "";
  }
}

// The cursor over the stage's items: the marked item in the evidence list, the column's
// control, and the PDF column on that item's page.
function stageCursor(i) {
  if (!STAGE_ITEMS.length) return;
  STAGE_CUR = Math.max(0, Math.min(STAGE_ITEMS.length - 1, i));
  document.querySelectorAll(".stg-evidence > .evi-item[data-i]").forEach((el) => el.classList.toggle("cur", Number(el.dataset.i) === STAGE_CUR));
  const el = document.querySelector(`.stg-evidence > .evi-item[data-i="${STAGE_CUR}"]`);
  if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  stageShowItem();
}
// The button at the foot of the stage panel: the next item while there is one, else the next
// stage, else "done" (the last stage, last item: it only scrolls back to the top).
function stageFootState() {
  const n = STAGE_ITEMS.length, last = ((stagesSpec() || {}).stages || []).length - 1;
  if (n && STAGE_CUR < n - 1) return "item";
  return STAGE < last ? "stage" : "done";
}
function updateStageFoot() {
  const b = document.querySelector(".stg-foot .stg-next"), p = document.querySelector(".stg-foot .stg-foot-prev");
  if (!b) return;
  const st = stageFootState();
  b.textContent = st === "item" ? "next ▸" : st === "stage" ? "next stage ▸" : "done";
  b.title = st === "item" ? "the next item of this stage (→)" : st === "stage" ? "the next stage (])" : "the last item of the last stage";
  if (p) p.disabled = !STAGE_ITEMS.length || STAGE_CUR <= 0;
}
function stageShowItem() {
  const k = document.querySelector(".stg-pane-nav .stg-pane-k");
  if (k) k.textContent = STAGE_ITEMS.length ? `item ${STAGE_CUR + 1} of ${STAGE_ITEMS.length}` : "no items";
  document.querySelectorAll(".stg-pane-nav button").forEach((b) => (b.disabled = !STAGE_ITEMS.length || (b.dataset.d === "-1" ? STAGE_CUR <= 0 : STAGE_CUR >= STAGE_ITEMS.length - 1)));
  updateStageFoot();
  STAGE_SHOWN = `${STAGE_KEY}|${STAGE_CUR}`;
  const g = STAGE_ITEMS[STAGE_CUR]; if (!g) return;
  const page = g.page || (g.ids.length ? (DATA.evidence[g.ids[0]] || {}).page : null);
  const located = g.ids.some((i) => EV.hasRect[i]);
  const pages = $("#pages"); if (!pages) return;
  // the page at whatever scale the coder set (no automatic zoom); the highlight's TOP a small
  // margin under the column's top edge, centred sideways; an unlocated item: the top of its page
  const token = ++STAGE_GO;                                // a later move cancels the retries of this one
  const go = () => {
    if (token !== STAGE_GO) return;
    const pb = pages.getBoundingClientRect();
    if (located) {
      const rects = jumpToEvidence(page, g.ids, { flash: false, scroll: false });
      const r = rects.slice().sort((x, y) => x.getBoundingClientRect().top - y.getBoundingClientRect().top)[0];
      if (!r) return;
      const rb = r.getBoundingClientRect();
      pages.scrollTo({ top: Math.max(0, pages.scrollTop + (rb.top - pb.top) - 24),
                       left: Math.max(0, pages.scrollLeft + (rb.left - pb.left) - pages.clientWidth / 2 + rb.width / 2), behavior: "smooth" });
    } else if (page) {
      const wrap = document.getElementById(`page-${page}`); if (!wrap) return;
      pages.scrollTo({ top: Math.max(0, pages.scrollTop + (wrap.getBoundingClientRect().top - pb.top) - 8), left: 0, behavior: "smooth" });
    }
  };
  requestAnimationFrame(go);
  if (located) {
    setTimeout(go, 500);                                   // the rects appear once the page image has loaded
    // a page image that lands later shifts everything below it: anchor again as each one arrives
    pages.querySelectorAll("img.pageimg").forEach((img) => { if (!img.complete) img.addEventListener("load", () => setTimeout(go, 60), { once: true }); });
  }
}
function stageLevel(st) {
  if (st.paper_fields) return { scope: "paper", names: new Set(st.paper_fields), child: null };
  if (st.fields) return { scope: "entry", names: new Set(st.fields), child: null };
  return { scope: "child", names: new Set(st.child_fields || []), child: VM.entries.children.find((c) => c.key === st.child) || null };
}
function fieldLabel(name) {
  const info = VM.fieldIndex.get(name);
  return info && info.field ? info.field.label || formatKey(name) : name;
}
const stageLetter = (i) => String.fromCharCode(97 + (i % 26));

// A stage's auto_id for a new entry or row: `first` when no sibling has a value in the field,
// else `next` with the smallest number no sibling uses ({first: "C", next: "T{n}"}: C, T1, T2 …).
function autoIdValue(ai, siblings) {
  const used = new Set(siblings.map((v) => (v === null || v === undefined ? "" : String(v).trim())).filter(Boolean));
  if (ai.first !== undefined && ai.first !== null && !used.size) return String(ai.first);
  for (let n = 1; n < 10000; n++) {
    const v = String(ai.next).replace("{n}", String(n));
    if (!used.has(v)) return v;
  }
  return null;
}
function stageEntryAutoId(st) {
  const ai = st.auto_id; if (!ai || !ai.field) return null;
  const v = autoIdValue(ai, (DATA.records || []).map((r) => (r.field_values || {})[ai.field]));
  return v === null ? null : { [ai.field]: v };
}

function addEntryButton() {
  const b = document.createElement("button");
  b.type = "button"; b.className = "stg-addtask chain-addres";
  b.textContent = `＋ add ${VM.entries.label.toLowerCase()}`;
  b.title = `a new ${VM.entries.label.toLowerCase()}, blank, every cell editable`;
  return b;
}

// The copy actions of a child stage: "copy <children> from <another entry>" and, where the stage
// declares copy_from_sibling, one "same as <reference>" button per row that is not the reference.
function stageCopyControls(st, L, rec, recs) {
  const key = L.child.key, label = (L.child.label || key).toLowerCase();
  const rows = (rec.field_values || {})[key];
  const others = recs.filter((r) => r !== rec && Array.isArray((r.field_values || {})[key]) && r.field_values[key].length);
  let html = "";
  if (others.length) html += `<label class="muted">copy ${esc(label)}s from <select class="stg-copyfrom" data-rid="${esc(rec.id)}" title="a deep copy of that ${esc(VM.entries.label.toLowerCase())}'s ${esc(label)}s, replacing these">`
    + `<option value="">…</option>${others.map((r) => `<option value="${esc(r.id)}">${esc(entryTitle(VM, r, r.entry_index))} (${r.field_values[key].length})</option>`).join("")}</select></label>`;
  const cs = st.copy_from_sibling;
  if (cs && Array.isArray(rows)) {
    const ref = rows.findIndex((r) => r && r[cs.when] === true);
    if (ref >= 0) {
      const refTitle = childTitle(L.child, rows[ref], ref);
      html += rows.map((r, j) => j === ref || !r ? "" : `<button type="button" class="stg-same" data-rid="${esc(rec.id)}" data-row="${j}" title="copy ${esc(cs.fields.map(fieldLabel).join(", "))} from ${esc(refTitle)}">${esc(childTitle(L.child, r, j))}: same as ${esc(refTitle)}</button>`).join("");
    }
  }
  return html ? `<div class="stg-copy">${html}</div>` : "";
}

// The stage's own cells: the paper fields; or, per entry, a strip (title, status, verify / flag,
// history, delete) with the stage's entry fields, or with the child's rows and the copy actions.
function stageCellsBlock(st, L, recs) {
  const box = document.createElement("div"); box.className = "stg-cells";
  box.innerHTML = `<div class="evi-sub stg-head">Fields</div>`;
  const unit = VM.entries.label.toLowerCase();
  if (L.scope === "paper") { box.insertAdjacentHTML("beforeend", eviPaperBlock([...L.names])); return box; }
  if (!recs.length) {
    box.insertAdjacentHTML("beforeend", `<div class="stg-empty muted">${L.scope === "entry" ? `No ${esc(unit)} yet — add one for each the paper reports.` : `Add a ${esc(unit)} first.`}</div>`);
    if (L.scope === "entry") box.appendChild(addEntryButton());
    return box;
  }
  for (const rec of recs) {
    const card = eviStripEl(rec); card.classList.add("evi-entry", "stg-entry");
    const fv = rec.field_values || {};
    if (L.scope === "entry") {
      const defs = VM.entries.fields.filter((f) => L.names.has(f.name));
      card.insertAdjacentHTML("beforeend", `<div class="rv-root stg-fields">${renderFields(defs, fv, "", { editable: true })}</div>`);
    } else if (L.child) {
      const rows = fv[L.child.key];
      card.insertAdjacentHTML("beforeend", `<div class="rv-root stg-fields"><div class="rv-row rv-row-block rv-decl" data-field="${esc(L.child.key)}">`
        + `<div class="rv-key" data-path="${esc(L.child.key)}" title="${esc(L.child.help || "")}">${esc(L.child.label || formatKey(L.child.key))}${Array.isArray(rows) ? ` <span class="muted">(${rows.length})</span>` : ""}</div>`
        + `<div class="rv-val rv-nested">${renderChild(L.child, rows, { editable: true, show: L.names })}</div></div></div>`
        + stageCopyControls(st, L, rec, recs));
    }
    box.appendChild(card);
  }
  if (L.scope === "entry") box.appendChild(addEntryButton());
  return box;
}

function renderStagesPanel(panel, rv) {
  const stages = rv.stages || [];
  STAGE = Math.max(0, Math.min(STAGE, stages.length - 1));
  const st = stages[STAGE] || {}, L = stageLevel(st);
  const unit = VM.entries.label.toLowerCase();
  const units = unit.endsWith("s") ? unit : `${unit}s`;      // a label given in the plural ("Tasks") stays as it is
  renderReviewHead(panel);
  const pr = docProgress();
  const confirmed = DATA._sentinel && DATA._sentinel.verification_status === "verified";
  const bar = document.createElement("div"); bar.className = "stg-bar";
  bar.innerHTML = `<button type="button" class="chain-nav stg-prev" title="previous stage ([)"${STAGE > 0 ? "" : " disabled"}>‹</button>`
    + stages.map((s, i) => `<button type="button" class="stg-tab${i === STAGE ? " active" : ""}" data-i="${i}" title="${esc(s.help || "")}"><span class="stg-k">${stageLetter(i)}</span> ${esc(s.label || s.id)}</button>`).join("")
    + `<button type="button" class="chain-nav stg-next" title="next stage (])"${STAGE < stages.length - 1 ? "" : " disabled"}>›</button>`
    + `<span class="stg-prog muted">${pr.total ? (pr.done ? `✓ every ${esc(unit)} verified` : `${pr.reviewed} / ${pr.total} ${esc(units)} verified`) : `no ${esc(units)} yet`}</span>`
    + (pr.total && !pr.done ? `<button type="button" class="btn btn-ghost" id="stg-verifyall" title="mark every unreviewed ${esc(unit)} verified — the paper is done when all are">✓ verify all ${esc(units)}</button>` : "")
    + (!pr.total && DATA._sentinel ? (confirmed ? `<span class="status verified" id="noconfirm">✓ Confirmed — no ${esc(units)}</span>` : `<button type="button" class="btn btn-ghost" id="noconfirm" title="this paper genuinely reports no ${esc(unit)}">✓ Confirm — no ${esc(units)} here</button>`) : "");
  panel.appendChild(bar);
  if (st.help) { const help = document.createElement("div"); help.className = "stg-help"; help.innerHTML = renderMarkdown(st.help); panel.appendChild(help); }
  const { groups, parsed, recs } = mergedItems(rv);
  const inStage = groups.filter((g) => [...g.names].some((n) => L.names.has(n)));
  const rest = groups.filter((g) => !inStage.includes(g));
  // the cursor: on the first item when a stage opens (or the paper changes), else where it was
  const key = `${DOCID}|${STAGE}`;
  if (STAGE_KEY !== key) { STAGE_KEY = key; STAGE_CUR = 0; }
  STAGE_ITEMS = inStage;
  STAGE_CUR = Math.max(0, Math.min(inStage.length - 1, STAGE_CUR));
  // EVIDENCE, then FIELDS
  const evbox = document.createElement("div"); evbox.className = "stg-evidence";
  evbox.innerHTML = `<div class="evi-sub stg-head">Evidence${inStage.length ? ` · ${inStage.length} item${inStage.length === 1 ? "" : "s"}` : ""}</div>`;
  inStage.forEach((g, i) => {
    const el = renderEviItem(g, parsed.get(g.n), recs, null, { bare: true });
    el.dataset.i = String(i); if (i === STAGE_CUR) el.classList.add("cur");
    el.title = "click: show in the PDF (→ / ← move on)";
    evbox.appendChild(el);
  });
  if (!inStage.length) evbox.insertAdjacentHTML("beforeend", `<p class="muted stg-noitems">No item bears on this stage's fields; judge them from the PDF.</p>`);
  if (rest.length) {
    const det = document.createElement("details"); det.className = "stg-other";
    det.innerHTML = `<summary>other evidence (${rest.length})</summary>`;
    for (const g of rest) det.appendChild(renderEviItem(g, parsed.get(g.n), recs, null, { bare: true }));
    evbox.appendChild(det);
  }
  panel.appendChild(evbox);
  panel.appendChild(stageCellsBlock(st, L, recs));
  const foot = document.createElement("div"); foot.className = "stg-foot";
  foot.innerHTML = `<button type="button" class="chain-nav stg-foot-prev" title="previous item (←)">◂</button><button type="button" class="btn btn-ghost stg-next"></button>`;
  panel.appendChild(foot);
  wireEvidencePanel(panel, recs);
  wireStagePanel(panel, recs, st, L);
  evbox.querySelectorAll(":scope > .evi-item[data-i]").forEach((el) => {
    const g = inStage[Number(el.dataset.i)];
    el.addEventListener("click", (e) => { if (e.target.closest("button, a, select, input, [contenteditable]")) return; stageCursor(Number(el.dataset.i)); });
    if (g.ids.length) { el.addEventListener("mouseenter", () => showEvidence(g.ids)); el.addEventListener("mouseleave", () => hideEvidence(g.ids)); }
  });
  setContextEvidence(inStage.flatMap((g) => g.ids));
  if (STAGE_SHOWN !== `${key}|${STAGE_CUR}`) stageShowItem();     // a new stage or paper: the PDF moves; an edit's redraw leaves it
}

// Redraw the panel after a structural edit, keeping the scroll position.
function rerenderKeep() {
  const tops = ["#panel", "#panelbody"].map((s) => [$(s), $(s) ? $(s).scrollTop : 0]);
  renderPanel();
  for (const [el, top] of tops) if (el) el.scrollTop = top;
}
function stageStep(dir) {
  const n = ((stagesSpec() || {}).stages || []).length;
  STAGE = Math.max(0, Math.min(n - 1, STAGE + dir));
  renderPanel();
  for (const s of ["#panel", "#panelbody"]) if ($(s)) $(s).scrollTop = 0;
}

async function verifyAll(btn) {
  btn.disabled = true;
  try {
    for (const rec of DATA.records || []) {
      if (rec.verification_status === "verified" || rec.verification_status === "flagged") continue;
      await api.verify(rec.id, { status: "verified" });
      rec.verification_status = "verified";
    }
    maybeOfferRepublish(); refreshNavState(); rerenderKeep();
  } catch (e) { btn.disabled = false; alert("verify failed: " + e.message); }
}

function wireStagePanel(panel, recs, st, L) {
  const byId = new Map(recs.map((r) => [r.id, r]));
  panel.querySelectorAll(".stg-tab").forEach((b) => (b.onclick = () => stageStep(+b.dataset.i - STAGE)));
  const prev = panel.querySelector(".stg-prev"), next = panel.querySelector(".stg-next");
  if (prev) prev.onclick = () => stageStep(-1);
  if (next) next.onclick = () => stageStep(1);
  panel.querySelectorAll(".stg-addtask").forEach((b) => (b.onclick = () => doAddFinding(stageEntryAutoId(st))));
  const nx = panel.querySelector(".stg-foot .stg-next"), pv = panel.querySelector(".stg-foot .stg-foot-prev");
  if (nx) nx.onclick = () => {
    const fs = stageFootState();
    if (fs === "item") stageCursor(STAGE_CUR + 1);
    else if (fs === "stage") stageStep(1);
    else for (const sel of ["#panel", "#panelbody"]) if ($(sel)) $(sel).scrollTo({ top: 0, behavior: "smooth" });
  };
  if (pv) pv.onclick = () => stageCursor(STAGE_CUR - 1);
  updateStageFoot();
  const va = panel.querySelector("#stg-verifyall"); if (va) va.onclick = () => verifyAll(va);
  const nc = panel.querySelector("button#noconfirm"); if (nc) nc.onclick = () => confirmNoRecords(nc);
  const ai = st.auto_id;
  const fill = ai && ai.field && L.child ? (path, rows) => (path === L.child.key ? { [ai.field]: autoIdValue(ai, rows.map((r) => (r || {})[ai.field])) } : null) : null;
  panel.querySelectorAll(".stg-cells .evi-entry[data-rid]").forEach((box) => {
    const rec = byId.get(box.dataset.rid); if (rec) wireTableRows(box, rec, rerenderKeep, fill);
  });
  panel.querySelectorAll(".stg-copyfrom").forEach((sel) => (sel.onchange = async () => {
    const rec = byId.get(sel.dataset.rid), src = byId.get(sel.value); sel.value = "";
    if (!rec || !src || !L.child) return;
    const key = L.child.key, label = (L.child.label || key).toLowerCase();
    const have = Array.isArray((rec.field_values || {})[key]) ? rec.field_values[key].length : 0;
    if (have && !confirm(`Replace the ${have} ${label}${have === 1 ? "" : "s"} of ${entryTitle(VM, rec, rec.entry_index)} with a copy of those of ${entryTitle(VM, src, src.entry_index)}? The change is logged.`)) return;
    const was = JSON.parse(JSON.stringify((rec.field_values || {})[key] || []));
    const copy = stripRowIds(JSON.parse(JSON.stringify(src.field_values[key])));
    if (!rec.field_values) rec.field_values = {};
    rec.field_values[key] = copy;
    try {
      await saveWholeRecord(rec, [{ field_path: key, original_value: `(${have} ${label}${have === 1 ? "" : "s"})`, final_value: `(${copy.length} ${label}${copy.length === 1 ? "" : "s"} copied from ${entryTitle(VM, src, src.entry_index)})` }]);
      maybeOfferRepublish(); rerenderKeep();
    } catch (e) { rec.field_values[key] = was; alert("copy failed: " + e.message); }
  }));
  panel.querySelectorAll(".stg-same").forEach((b) => (b.onclick = async () => {
    const rec = byId.get(b.dataset.rid), j = Number(b.dataset.row), cs = st.copy_from_sibling;
    if (!rec || !cs || !L.child) return;
    const rows = (rec.field_values || {})[L.child.key] || [];
    const ref = rows.find((r) => r && r[cs.when] === true), row = rows[j];
    if (!ref || !row) return;
    const diff = [], was = {};
    for (const f of cs.fields) {
      const from = ref[f] === undefined ? null : ref[f], to = row[f] === undefined ? null : row[f];
      if (JSON.stringify(from) === JSON.stringify(to)) continue;
      was[f] = row[f];
      diff.push({ field_path: `${L.child.key}[${j}].${f}`, original_value: to, final_value: from });
      row[f] = JSON.parse(JSON.stringify(from));
    }
    if (!diff.length) return;
    try { await saveWholeRecord(rec, diff); maybeOfferRepublish(); rerenderKeep(); }
    catch (e) { for (const [f, v] of Object.entries(was)) row[f] = v; alert("copy failed: " + e.message); }
  }));
}

// The sub-entries (arms, results …) are added and edited on the generic tabs: switch to the
// fields view of this entry and open the tab that holds the sub-entry.
function eviOpenFields(rec, childKey) {
  CHAIN_OFF = true;
  const idx = (DATA.records || []).indexOf(rec);
  if ((DATA.records || []).length > 1 && idx >= 0) PANEL_SEL = idx;
  renderPanel();
  const card = document.querySelector(`.record[data-rid="${rec.id}"]`); if (!card) return;
  const ti = VM.tabs.findIndex((t) => t.fields.includes(childKey));
  const tabs = card.querySelectorAll(".subtab");
  if (ti >= 0 && tabs[ti]) tabs[ti].click();
  card.scrollIntoView({ block: "start", behavior: "smooth" });
}

// ── change history (verification events, across sessions) ────────────────────
async function toggleHistory(card, rec) {
  const body = card.querySelector(".histbody");
  if (!body.hidden) { body.hidden = true; return; }
  body.hidden = false; body.innerHTML = '<span class="muted">loading…</span>';
  try {
    const { events } = await api.recordEvents(rec.id);
    body.innerHTML = events.length ? events.map(fmtEvent).join("")
      : '<span class="muted">no changes recorded yet</span>';
  } catch (e) { body.innerHTML = `<span class="muted">${esc(e.message)}</span>`; }
}
function fmtEvent(e) {
  const who = e.verifier_email || e.verifier_kind || "someone";
  const when = (e.created_at || "").replace("T", " ").slice(0, 16);
  const changes = Array.isArray(e.diff)
    ? e.diff.filter((d) => d.original_value !== d.final_value).map((d) =>
        `<div class="hist-diff"><code>${esc(d.field_path)}</code>: `
        + `${esc(String(d.original_value))} → <b>${esc(String(d.final_value))}</b></div>`).join("")
    : "";
  const note = e.notes ? `<div class="hist-note">${esc(e.notes)}</div>` : "";
  return `<div class="hist-row"><span class="hist-meta">${esc(e.status)} · ${esc(who)} · ${esc(when)}</span>${note}${changes}</div>`;
}

// ── value ↔ evidence linking ────────────────────────────────────────────────
// evidence.field_path is full ("records[0].estimates._table[0].coefficient");
// grammar value cells carry a record-relative data-path. Strip the core prefix.
function stripCore(fp) {
  if (!fp) return "";
  return fp.replace(/^[a-zA-Z_][a-zA-Z0-9_]*(?:\._table)?\[\d+\]\.?/, "").replace(/\._table\[/g, "[");
}

// Tokenise a record-relative data-path — "a", "a.b", "a[0].b", "a._table[0].c" — into keys
// and indices. Declared field names never contain "." or "[", so this is exact for them.
function pathTokens(path) {
  const toks = [];
  String(path).replace(/[^.[\]]+|\[(\d+)\]/g, (m, idx) => (toks.push(idx !== undefined ? Number(idx) : m), ""));
  return toks;
}
function setByPath(obj, path, value) {
  const toks = pathTokens(path);
  if (!toks.length) return;
  let cur = obj;
  for (let k = 0; k < toks.length - 1; k++) {
    const key = toks[k];
    if (cur[key] == null || typeof cur[key] !== "object") cur[key] = typeof toks[k + 1] === "number" ? [] : {};
    cur = cur[key];
  }
  cur[toks[toks.length - 1]] = value;
}
function getByPath(obj, path) {
  let cur = obj;
  for (const t of pathTokens(path)) { if (cur == null || typeof cur !== "object") return undefined; cur = cur[t]; }
  return cur;
}

// Persist ONE field correction through the verify layer — so the change lands in the record
// (→ export + reload) and the audit trail, not just the UI. Shared by text cells + typed
// controls. Returns the save promise.
//
// The page's copy of the record changes AT ONCE, and only the edited cell travels: the
// server sets it on the record as stored, so saves of different cells may cross on the wire
// and all land. Two edits of the SAME cell go out in order; a structural edit (the whole
// record) waits for everything before it and holds everything after it. (Before, each save
// sent the whole record as the page knew it, updated only when the server had answered — so
// two quick edits raced, the later one carrying the earlier cell's old value, and the server
// kept that: edits "came back" on the next reload.)
const PENDING = new Map();           // record id → the saves of it still in flight
const LAST = new Map();              // "record|cell" or "record|*" → the save a next one of it waits for
function track(recId, key, p) {
  const set = PENDING.get(recId) || new Set(); PENDING.set(recId, set); set.add(p);
  LAST.set(key, p);
  p.catch(() => {}).then(() => { set.delete(p); if (!set.size) PENDING.delete(recId); if (LAST.get(key) === p) LAST.delete(key); });
  return p;
}
function queueSave(recId, path, fn) {
  const after = [LAST.get(`${recId}|${path}`), LAST.get(`${recId}|*`)].filter(Boolean);
  return track(recId, `${recId}|${path}`, Promise.allSettled(after).then(fn));
}
// what is still on its way to the server — a paper switch waits for it before re-reading
function pendingSaves() { return Promise.allSettled([...PENDING.values()].flatMap((set) => [...set])); }
// leaving the page with a save still out would lose it: ask first
window.addEventListener("beforeunload", (e) => { if (PENDING.size) { e.preventDefault(); e.returnValue = ""; } });
// After the first review action in a published dataset, offer once to push the changes as a
// new version — the GitHub copy is what people cite, so it should not silently drift.
let REPUBLISH_OFFERED = false;
async function maybeOfferRepublish() {
  if (!PROJECT || REPUBLISH_OFFERED) return;
  REPUBLISH_OFFERED = true;
  try {
    const ov = await api.datasetOverview(PROJECT);
    if (ov.publish_status !== "published") return;
    const v = (ov.version || 1) + 1;
    if (!confirm(`This dataset is published on GitHub. Publish your changes as version ${v} when you are done?\n\nOK opens the pull request now; Cancel leaves the published copy as it is (you can publish the update from the dataset page later).`)) return;
    await api.publishDataset(PROJECT, { target: ov.catalogue === false ? "github" : "github+metalens" });
    alert(`Pull request opened for version ${v}. The catalogue switches to it once merged.`);
  } catch { /* offer only; never block the save */ }
}

function saveFieldEdit(rec, card, path, newVal, origVal) {
  if (!rec.field_values) rec.field_values = {};
  setByPath(rec.field_values, path, newVal);        // now — later saves and renders see it
  // A value edit is a CORRECTION (logged for provenance/audit), NOT a verification —
  // it must not mark the record "verified"; that stays the explicit ✓ button's job.
  return queueSave(rec.id, path, () => api.verify(rec.id, {
    status: "corrected",
    diff: [{ field_path: path, original_value: origVal, final_value: newVal }],
    patch: [{ field_path: path, value: newVal }],
  }, { keepalive: true })).then(() => maybeOfferRepublish())
    .catch((e) => { setByPath(rec.field_values, path, origVal); alert("save failed: " + e.message); });
}
// current stored value at a record-relative path (nested paths included) — the diff's original_value
function curVal(rec, path) { return getByPath(rec.field_values || {}, path); }

// Wire the typed controls (+ free-text cells when textToo) inside a container to
// onSave(path, value). Shared by record cards (per-record save), the paper panel and the
// legacy constant block. Handles select, allow_other, boolean selects, and multi-select.
function wireControls(container, onSave, textToo) {
  if (textToo) container.querySelectorAll(".rv-editable").forEach((cell) => {
    if (cell.classList.contains("study-ident")) return;   // identity has its own handler
    cell.dataset.orig = cell.textContent;
    cell.addEventListener("blur", () => {
      const now = cell.textContent;
      if (now === cell.dataset.orig) return;
      cell.classList.add("rv-edited");
      cell.classList.toggle("rv-empty", now.trim() === "");
      // cleared → null (not ""); a number typed into a numeric cell → number; else text
      const val = now.trim() === "" ? null
        : cell.classList.contains("rv-num") && !isNaN(Number(now)) ? Number(now) : now;
      Promise.resolve(onSave(cell.dataset.path, val)).then(() => (cell.dataset.orig = now));
    });
  });
  container.querySelectorAll(".rv-selwrap").forEach((wrap) => {
    const path = wrap.dataset.path, sel = wrap.querySelector(".rv-select"), other = wrap.querySelector(".rv-other");
    const isBool = wrap.classList.contains("rv-bool");
    const commit = (val) => { wrap.classList.add("rv-edited"); onSave(path, val); };
    sel.onchange = () => {
      if (sel.value === "__other__") { if (other) { other.hidden = false; other.focus(); } return; }
      if (other) other.hidden = true;
      if (isBool) { commit(sel.value === "" ? null : sel.value === "true"); return; }
      // keep the declared option's own type (1 stays a number, "rct" a string)
      const raw = sel.value || null;
      commit(raw !== null && /^-?\d+(\.\d+)?$/.test(raw) && [...sel.options].some((o) => o.value === raw && !isNaN(Number(raw))) ? Number(raw) : raw);
    };
    if (other) other.addEventListener("blur", () => { if (sel.value === "__other__") commit(other.value.trim() || null); });
  });
  container.querySelectorAll(".rv-multi").forEach((mbox) => {
    const path = mbox.dataset.path;
    const commit = () => { mbox.classList.add("rv-edited"); onSave(path, multiValues(mbox)); };
    // one listener on the box: a chip added later (a typed term, a synced twin) is covered too
    mbox.addEventListener("change", (e) => {
      const cb = e.target;
      if (!cb || cb.type !== "checkbox") return;
      if (cb.dataset.free && !cb.checked) cb.closest(".rv-chk").remove();   // an unticked term leaves the list
      commit();
    });
    const other = mbox.querySelector(".rv-multi-other");
    if (other) {
      const add = () => {
        const term = other.value.trim(); other.value = "";
        if (!term) return;
        const box = [...mbox.querySelectorAll('input[type="checkbox"]')].find((cb) => cb.value === term);
        if (box) { if (!box.checked) { box.checked = true; commit(); } return; }   // a declared code typed out: tick it
        other.insertAdjacentHTML("beforebegin", freeChip(term));
        commit();
      };
      other.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
      other.addEventListener("blur", add);
    }
  });
}

// A multi control's value: the ticked boxes in order — declared codes and the paper's own terms
// in one list. Setting a value ticks the declared boxes, drops the chips of terms no longer
// listed and adds a chip for every term that has no box, so twins of one cell agree.
function multiValues(mbox) {
  return [...mbox.querySelectorAll('input[type="checkbox"]:checked')].map((x) => x.value);
}
function multiSetValues(mbox, val) {
  const vals = (Array.isArray(val) ? val : []).map(String);
  mbox.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
    if (cb.dataset.free) { if (vals.includes(cb.value)) cb.checked = true; else cb.closest(".rv-chk").remove(); }
    else cb.checked = vals.includes(cb.value);
  });
  const have = new Set([...mbox.querySelectorAll('input[type="checkbox"]')].map((cb) => cb.value));
  const anchor = mbox.querySelector(".rv-multi-other");
  for (const x of vals) {
    if (have.has(x)) continue;
    if (anchor) anchor.insertAdjacentHTML("beforebegin", freeChip(x)); else mbox.insertAdjacentHTML("beforeend", freeChip(x));
    have.add(x);
  }
}

// A page-REFERENCE field (page / evidence_page / *_page) holds a page number, not a value
// that lives in the PDF — clicking it must never hunt that number (it would light up every
// matching digit). It still jumps to its covering (e.g. table-caption) evidence.
function isPageRef(path) {
  const leaf = (path || "").split(".").pop().replace(/\[\d+\]$/, "");
  return leaf === "page" || leaf === "evidence_page" || leaf.endsWith("_page");
}

// Declared documents: bind every cell to the most specific evidence that supports it —
// its own citation, else its row's, its table's, its entry's — and mark its citation
// state. A declared "value" field with nothing is visibly uncited. Orphans never attach.
function linkCells(card, rec) {
  card.querySelectorAll("[data-path]").forEach((cell) => {
    const p = cell.dataset.path;
    const leaf = p.split(".").pop().replace(/\[\d+\]$/, "");
    const info = VM.fieldIndex.get(leaf);
    const def = info && info.field ? info.field : null;
    const hit = evidenceFor(EV, VM, rec.id, p);
    const isControl = cell.classList.contains("rv-selwrap") || cell.classList.contains("rv-multi");
    const editable = cell.classList.contains("rv-editable") || isControl;
    const isKey = cell.classList.contains("rv-key");
    const hasValue = !isKey && (cell.classList.contains("rv-editable") || cell.classList.contains("rv-cell") || isControl)
      && !cell.classList.contains("rv-missing") && (isControl || cell.textContent.trim() !== "");
    // A field declared `evidence: value` must carry its OWN citation; the entry's or row's
    // identifying snippet covers `row` fields, not a number the coder has to check.
    const wantsOwn = def && def.evidence === "value";
    if (hit) {
      const exact = hit.kind === "exact";
      const anyRect = hit.ids.some((i) => EV.hasRect[i]);
      if (isKey) cell.classList.add("rv-linked");
      else if (exact) cell.classList.add(anyRect ? "rv-cited" : "rv-cited-nofix");
      else if (wantsOwn && hasValue) cell.classList.add("rv-uncited", "rv-covered");
      else cell.classList.add("rv-covered");
      cell.addEventListener("mouseenter", () => showEvidence(hit.ids));
      cell.addEventListener("mouseleave", () => hideEvidence(hit.ids));
      cell.addEventListener("click", () => verifyAndJump(cell, { ids: hit.ids, page: DATA.evidence[citeFor(cell, hit.ids)].page, exact, kind: hit.kind, locate: tableLocate(rec, p) }));
      return;
    }
    if (wantsOwn && hasValue) cell.classList.add("rv-uncited");
    // No cited evidence. Verbatim value-search is NUMERIC-only, so only numbers stay
    // clickable-to-locate; a text value with no cited snippet has nothing to jump to.
    if (cell.classList.contains("rv-num") && !isPageRef(p) && (!def || def.evidence !== "none")) {
      if (!editable) cell.classList.add("rv-probe");
      cell.addEventListener("click", () => locateAndFlash(cell));
    }
  });
  // row / table citation chips
  card.querySelectorAll(".rv-rowcite[data-rowpath]").forEach((slot) => {
    const rp = slot.dataset.rowpath;
    const ids = EV.rowCites.get(`${rec.id}|${rp}`);
    const cconf = (rec.child_confidence || {})[rp];
    let html = "";
    if (ids) html += `<button type="button" class="ev-cite" data-eids="${ids.join(",")}" data-page="${DATA.evidence[ids[0]].page || 1}" title="${esc(DATA.evidence[ids[0]].snippet || "")}">p.${DATA.evidence[ids[0]].page || "?"}</button>`;
    else html += `<span class="ev-cite ev-none" title="no citation for this row">–</span>`;
    if (cconf) {
      const worst = worstLevel(VM, cconf);
      html += renderConfDot(worst, VM.levels, Object.entries(cconf).map(([g, r]) => `${(VM.groups[g] || {}).label || g}: ${r.level}${r.notes ? ` — ${r.notes}` : ""}`).join("\n"));
    }
    slot.innerHTML = html;
  });
  card.querySelectorAll(".rc-conf[data-rowpath]").forEach((slot) => {
    const cconf = (rec.child_confidence || {})[slot.dataset.rowpath];
    if (!cconf) return;
    slot.innerHTML = Object.entries(cconf).map(([g, r]) => renderConfBadge(g, VM.groups[g], r, VM.levels)).join("");
  });
  wireConfBadges(card);
  card.querySelectorAll(".ev-cite[data-eids]").forEach((b) => {
    const ids = b.dataset.eids.split(",").map(Number);
    b.onclick = (e) => { e.stopPropagation(); jumpToEvidence(+b.dataset.page, ids); };
    b.onmouseenter = () => showEvidence(ids);
    b.onmouseleave = () => hideEvidence(ids);
  });
  // low-confidence fields: the coder looks there first
  card.querySelectorAll("[data-group]").forEach((el) => {
    const r = (rec.confidence || {})[el.dataset.group];
    if (!r || r.level == null) return;
    if (isLow(VM, r.level)) el.classList.add("rv-low");
    else if (VM.levels.indexOf(String(r.level)) > 0) el.classList.add("rv-medium");
  });
  // per sub-entry card (a condition …): what has NO evidence, summarised where the coder
  // looks — not only as markers on the individual cells
  card.querySelectorAll(".rv-card.rv-childrow[data-rowpath]").forEach((cc) => {
    const rp = cc.dataset.rowpath;
    const pre = `${rec.id}|${rp}`;
    const has = (m) => [...m.keys()].some((k) => k === pre || k.startsWith(pre + ".") || k.startsWith(pre + "["));
    const wrap = cc.closest("[data-child]");
    const info = wrap ? VM.fieldIndex.get(wrap.dataset.child) : null;
    const what = ((info && info.child && info.child.label) || "sub-entry").toLowerCase();
    const uv = cc.querySelectorAll(".rv-uncited").length;
    const ur = [...cc.querySelectorAll(".rv-rowcite[data-rowpath] .ev-none")].filter((e) => e.closest(".rv-rowcite").dataset.rowpath !== rp).length;
    cc.querySelectorAll(".rc-evsum").forEach((e) => e.remove());
    let msg = "";
    if (!has(EV.exact) && !has(EV.rowCites) && !has(EV.tableCites)) msg = `no evidence cited for this ${what}`;
    else if (uv || ur) msg = [uv ? `${uv} value${uv === 1 ? "" : "s"}` : "", ur ? `${ur} row${ur === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ") + " without evidence";
    if (msg) cc.querySelector(".rc-head").insertAdjacentHTML("afterend", `<div class="rc-evsum" title="cells marked “no evidence” have no citation of their own">⚠ ${esc(msg)}</div>`);
  });
}

// Legacy documents: the longest-prefix path match that predates declared evidence policy.
function linkValueCells(card, evs) {
  const linkable = evs.map(({ ev, i }) => ({ i, page: ev.page, path: stripCore(ev.field_path) }));
  card.querySelectorAll("[data-path]").forEach((cell) => {
    const p = cell.dataset.path;
    let best = null;                                   // most specific covering evidence
    for (const x of linkable) {
      const covers = x.path === "" || x.path === p || p.startsWith(x.path + ".") || p.startsWith(x.path + "[");
      if (covers && (!best || x.path.length > best.path.length)) best = x;
    }
    const isControl = cell.classList.contains("rv-selwrap") || cell.classList.contains("rv-multi");
    const editable = cell.classList.contains("rv-editable") || isControl;
    if (best) {
      cell.addEventListener("mouseenter", () => showEvidence(best.i));
      cell.addEventListener("mouseleave", () => hideEvidence(best.i));
      if (!editable) cell.classList.add("rv-linked");
      cell.addEventListener("click", () => verifyAndJump(cell, { ids: [best.i], page: best.page, exact: best.path === p }));
      return;
    }
    if (cell.classList.contains("rv-num") && !isPageRef(p)) {
      if (!editable) cell.classList.add("rv-probe");
      cell.addEventListener("click", () => locateAndFlash(cell));
    }
  });
}

// A NUMERIC value with no model-cited evidence → best-effort: find that exact number in
// the PDF and flash it. Verbatim search is numeric-only (text values jump to their cited
// snippet instead, never a verbatim value hunt). Silent when the number isn't found.
async function locateAndFlash(cell) {
  const txt = (cell.textContent || "").trim();
  if (!NUM_RE.test(txt)) return;               // numeric-only
  try {
    const r = await api.locateValue(DATA.document_id, txt.replace(/%$/, ""), 1);
    if (r && r.found) flashRects(r.page, r.rects);
  } catch { /* best-effort */ }
}

// Click a value with cited evidence. The universal rule (applies to every preset):
//   (a) TEXT value    → jump to the cited evidence snippet and flash it.
//   (b) NUMERIC value → search the exact number on the evidence page and highlight IT
//       (numbers are what readers verify); fall back to the snippet if it isn't there
//       verbatim (rounded / transformed / computed — a soft note, not an error).
const NUM_RE = /^-?\d[\d,]*(\.\d+)?%?$/;
// A row cited several times (the results table, a descriptives table, a methods sentence):
// a click on a NUMBER lands on the citation whose snippet carries that number, so the
// band-limited chase below finds it; otherwise the first citation.
function citeFor(cell, ids) {
  const txt = cell.textContent.trim().replace(/%$/, "");
  if (!NUM_RE.test(txt) || ids.length < 2) return ids[0];
  const bare = txt.replace(/,/g, "");
  const hit = ids.find((i) => {
    const sn = String(DATA.evidence[i].snippet || "").replace(/,/g, "");
    return new RegExp(`(^|[^0-9.])${bare.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![0-9])`).test(sn);
  });
  return hit == null ? ids[0] : hit;
}

// A cell of a table field the preset lists under display.locate (numbers that are always
// printed in a table): {anchor} = the text that identifies the cell's printed row, e.g. the
// wording of its item, taken from the run's parameters. null for every other cell.
function tableLocate(rec, path) {
  const m = /(?:^|\.)(\w+)\[(\d+)\]\.(\w+)$/.exec(path || "");
  const rule = m && VM && VM.locate ? VM.locate[m[1]] : null;
  if (!rule) return null;
  let anchor = null;
  try {
    const row = ((rec.field_values || {})[m[1]] || [])[+m[2]] || {};
    const list = rule.anchor_param ? (DATA.params || {})[rule.anchor_param] : null;
    const k = rule.anchor_column ? row[rule.anchor_column] : null;
    if (Array.isArray(list) && Number.isInteger(+k) && list[+k - 1]) anchor = String(list[+k - 1]);
    // or the row's own text (how the paper names the two variables of an effect size)
    const own = (rule.anchor_fields || []).map((f) => row[f]).filter((v) => typeof v === "string" && v.trim());
    if (!anchor && own.length) anchor = own;
  } catch { /* no anchor */ }
  return { anchor };
}

async function verifyAndJump(cell, hit) {
  if (cell.nextElementSibling && cell.nextElementSibling.classList.contains("val-check"))
    cell.nextElementSibling.remove();
  // Jump to the cited snippet IMMEDIATELY — the rects are already in the DOM, so this is
  // instant. For a number we then refine to its exact location once the server search
  // returns; the user never waits on that round-trip to see the evidence.
  const txt = cell.textContent.trim();
  const chased = NUM_RE.test(txt) && !isPageRef(cell.dataset.path);
  // presets with display.citation_flash:false (MASEMiner): the cited table/row is shown
  // steadily and only the number found inside it blinks
  jumpToEvidence(hit.page, hit.ids, { flash: !(chased && VM && VM.citationFlash === false) });
  if (!chased) return;
  const num = txt.replace(/%$/, "");                 // keep commas; server tries both forms
  if (!hit.exact) {
    // A row citation: pinpoint the number INSIDE the cited row band(s) only — never the
    // whole page, which in a dense table would light up every matching digit. The row
    // stays lit; the number is marked on top of it. A table/entry-level citation just
    // lands on its snippet.
    if (hit.kind !== "row") {
      if (!hit.locate) return;
      // declared table numbers: on the row of the item's wording when known, else every
      // whole-number match on the cited page (candidates; the table citation stays lit)
      try {
        const r = await api.locateValue(DATA.document_id, num, hit.page, null, { anchor: hit.locate.anchor, pageOnly: true });
        if (r && r.found) flashRects(hit.page, r.rects, { keep: true });
      } catch { /* the table highlight stands */ }
      return;
    }
    const bands = hit.ids.flatMap((i) => (DATA.evidence[i].page === hit.page ? (DATA.evidence[i].rect || []) : []))
      .map(([, y, , h]) => `${Math.round(y)}:${Math.round(y + h)}`);
    if (!bands.length) return;
    try {
      const r = await api.locateValue(DATA.document_id, num, hit.page, bands.join(","));
      if (r && r.found) flashRects(hit.page, r.rects, { keep: true });
    } catch { /* the row highlight stands */ }
    return;
  }
  try {
    const r = await api.locateValue(DATA.document_id, num, hit.page);    // (b) refine to the number
    if (r && r.found) { flashRects(r.page || hit.page, r.rects); return; }   // highlight where it actually is
    if (r && r.no_pdf) return;                        // no PDF to search — the snippet jump stands
  } catch { return; }                                // network hiccup — the snippet jump stands
  // number isn't in the source verbatim → keep the snippet jump + a soft note
  cell.insertAdjacentHTML("afterend",
    `<span class="val-check" title="This exact number isn't in the source PDF text — it may be rounded, transformed, or computed from other values. Not necessarily wrong.">⚠ not found verbatim</span>`);
}

// ── download results (JSON / CSV) ───────────────────────────────────────────
function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
function baseName() { return (DATA.schema_id || "records").replace(/[^a-z0-9]+/gi, "_"); }
function downloadJSON() {
  const ex = (DATA.records || []).map((r) => r.extraction).find(Boolean) || {};
  const out = {
    exported_at: new Date().toISOString(), document_id: DATA.document_id, filename: DATA.filename,
    schema_id: DATA.schema_id,
    // what produced it: the model, the exact prompt (its hash identifies it; the text is what a
    // reader needs — null for imported documents) and the preset parameters it was rendered with
    recipe: { model: ex.model || null, resolved_model: ex.resolved_model || null,
              prompt_sha256: DATA.prompt_sha256 || ex.prompt_sha256 || null, prompt_edited: ex.prompt_edited ?? null,
              params: DATA.params || null, prompt: DATA.prompt || null },
    paper: DATA.paper, paper_metadata: DATA.paper_metadata,
    records: DATA.records.map((r) => stripRowIds(r.field_values)),
    // the model's self-assessment per entry, and provenance parallel to records[]: review
    // status + any human corrections (original→final, who, when) so a consumer can tell
    // model-extracted values from human-corrected ones.
    confidence: DATA.records.map((r) => ({ entry_index: r.entry_index, ...(r.confidence || {}), ...(Object.keys(r.child_confidence || {}).length ? { children: r.child_confidence } : {}) })),
    provenance: DATA.records.map((r) => ({
      entry_index: r.entry_index,
      verification_status: r.verification_status,
      corrections: r.corrections || [],
    })),
    evidence: DATA.evidence,
  };
  download(`${baseName()}.json`, JSON.stringify(out, null, 2), "application/json");
}
function flattenRecord(fv) {
  const out = {};
  (function walk(obj, prefix) {
    Object.entries(obj || {}).forEach(([k, v]) => {
      if (k === "evidence" || k === "extraction_confidence" || k === "confidence") return;
      const key = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === "object" && !Array.isArray(v) && !("_table" in v)) walk(v, key);
      else out[key] = v && typeof v === "object" ? JSON.stringify(v) : v;
    });
  })(fv, "");
  return out;
}
const csvCell = (v) => Array.isArray(v) ? v.map((x) => (x && typeof x === "object" ? JSON.stringify(x) : x)).join("; ")
  : (v && typeof v === "object" ? JSON.stringify(v) : v);
function downloadCSV() {
  const pm = DATA.paper_metadata || {};
  const paperCols = VM.paper.fields.map((f) => f.name);
  // the same rows as the grid: one per entry / sub-entry / table row (display.grid_rows),
  // parent values repeated, so the file is the flat table a meta-analysis consumes
  const rows = !VM.legacy ? gridRows(VM, DATA.records).map((row) => {
    const flat = {};
    paperCols.forEach((k) => (flat[`paper.${k}`] = pm[k]));            // paper-level fields on every row
    flat.entry = row.idx;                                             // "0", "0.1", "0.1.2"
    gridColumns(VM).forEach((c) => (flat[c.key] = csvCell(row.values[c.key])));
    Object.entries(row.conf || {}).forEach(([g, v]) => (flat[`confidence.${g}`] = v && v.level));
    flat.verification_status = row.rec.verification_status || "";    // provenance columns, appended last
    flat.corrected_fields = (row.rec.corrections || []).map((c) => c.field_path).join("; ");
    return flat;
  }) : DATA.records.map((r) => {
    const flat = {};
    paperCols.forEach((k) => (flat[`paper.${k}`] = pm[k]));
    Object.assign(flat, flattenRecord(r.field_values));
    Object.entries(r.confidence || {}).forEach(([g, v]) => (flat[`confidence.${g}`] = v && v.level));
    flat.verification_status = r.verification_status || "";
    flat.corrected_fields = (r.corrections || []).map((c) => c.field_path).join("; ");
    return flat;
  });
  download(`${baseName()}.csv`, toCSV(rows), "text/csv");
}
function toCSV(rows) {
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const q = (v) => { if (v == null) return ""; const s = String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => q(r[c])).join(","))].join("\n");
}

// The whole dataset as one file: the dataset export (every paper with its records and their
// review status), optionally cut to the records a person has reviewed (verified or flagged).
// The CSV has the same rows as the per-paper CSV — one per entry / sub-entry / table row with the
// paper's metadata repeated — when the dataset is on this paper's schema; otherwise one row per record.
const REVIEWED = new Set(["verified", "flagged"]);
async function downloadDataset(kind) {
  let out;
  try { out = await api.datasetExport(PROJECT); }
  catch (e) { alert("export failed: " + e.message); return; }
  const reviewed = kind.endsWith("reviewed");
  const papers = (out.papers || []).map((p) => ({ ...p, records: (p.records || []).filter((r) => !reviewed || REVIEWED.has(r.verification_status)) }))
    .filter((p) => !reviewed || p.records.length);
  const name = `${(out.slug || "dataset").replace(/[^a-z0-9-]+/gi, "_")}${reviewed ? "-reviewed" : ""}`;
  if (kind.startsWith("json")) {
    download(`${name}.json`, JSON.stringify({ ...out, ...(reviewed ? { filter: "reviewed records only (verified or flagged)" } : {}), papers }, null, 2), "application/json");
    return;
  }
  const sameSchema = !VM.legacy && out.recipe && out.recipe.schema_id === DATA.schema_id;
  const rows = [];
  for (const p of papers) {
    const pm = p.paper || {};
    const head = { "paper.title": pm.title, "paper.doi": pm.doi, "paper.year": pm.year, "paper.journal": pm.journal,
                   "paper.authors": Array.isArray(pm.authors) ? pm.authors.join("; ") : pm.authors, filename: p.filename };
    const recs = p.records.map((r) => ({ id: r.record_id, entry_index: r.entry_index, field_values: r.values || {},
                                          verification_status: r.verification_status, corrections: [] }));
    if (sameSchema) {
      for (const row of gridRows(VM, recs)) {
        const flat = { ...head, record_id: row.rec.id, entry: row.idx };
        gridColumns(VM).forEach((c) => (flat[c.key] = csvCell(row.values[c.key])));
        flat.verification_status = row.rec.verification_status || "";
        rows.push(flat);
      }
    } else {
      for (const r of recs) rows.push({ ...head, record_id: r.id, entry: String(r.entry_index), ...flattenRecord(r.field_values), verification_status: r.verification_status || "" });
    }
  }
  download(`${name}.csv`, toCSV(rows), "text/csv");
}

init();
