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
import { renderValue, renderFields, renderChild, renderConfBadge, renderConfDot, esc, formatKey, stripRowIds } from "/static/grammar.js";
import { renderPages, jumpToEvidence, showEvidence, hideEvidence, flashRects, setContextEvidence } from "/static/pdfview.js";
import { saveToWorkspace } from "/static/save.js";
import { renderGrid } from "/static/gridview.js";
import { viewModel, indexEvidence, evidenceFor, entryTitle, worstLevel, isLow, entryNeeds,
         undeclared, seedEntry, orderedEntries, gridColumns, gridRows } from "/static/spec.js";

const $ = (s, el = document) => el.querySelector(s);
let DATA = null, DOCS = [], DOCID = null, RAW = false, GRID = false, PROJECT = null, PROJECT_TITLE = "", FOCUS_REC = null;
let CHAIN_OFF = false;                                   // a chain-layout preset, but the reviewer wants the field view
let AUX = { dataset: null, cross: null, concepts: null };   // per dataset: cross-check verdicts, concept ids (chain layout)
let PANEL_SEL = null;   // multi-entry panel nav: null(default→"paper") | "paper" | record index
let JOBS = {};   // job_id -> {status:'pending'|'complete'|'failed', document_id?, error?} — this-round tracking
let JOBS_POLLED = false;   // suppress "extracting…" placeholders until we've checked real status once
let ACCOUNT = false;  // logged in? (exports need no account; kept for owner-only actions)
let VM = null, EV = null;   // the preset's view model + the evidence index for the loaded document
let TRIAGE = "order";       // entry nav order: order | low_conf | unverified | flagged
let AUTOADV = true;         // after ✓ / ⚑ jump to the next unreviewed entry
try { TRIAGE = localStorage.getItem("metalens_triage") || TRIAGE; AUTOADV = localStorage.getItem("metalens_autoadvance") !== "0"; } catch { /* */ }

// Drag the splitter to resize the entries panel; width persists across sessions.
function mountSplitter() {
  const sp = $("#splitter"), panel = $("#panel");
  if (!sp || !panel) return;
  const saved = parseInt(localStorage.getItem("metalens_panel_w") || "", 10);
  if (saved) panel.style.width = saved + "px";
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
    localStorage.setItem("metalens_panel_w", String(parseInt(panel.style.width, 10) || 480));
  });
}

// Zoom the PDF pages: − / + / fit buttons and Ctrl/⌘ + wheel over the pages. A factor
// above 1 makes a page wider than the pane (it scrolls sideways) — the way to read a page
// when the review panel takes most of the window. Persists across sessions.
function mountPdfZoom() {
  const pages = $("#pages"), box = $("#pdfzoom"), val = $("#pdfzoomval");
  if (!pages || !box) return;
  let z = parseFloat(localStorage.getItem("metalens_pdf_zoom") || "1") || 1;
  const apply = () => {
    z = Math.min(4, Math.max(0.5, Math.round(z * 100) / 100));
    pages.style.setProperty("--pdf-zoom", String(z));
    val.textContent = z === 1 ? "fit" : `${Math.round(z * 100)}%`;
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
      try { PROJECT_TITLE = (await api.dataset(PROJECT)).title || ""; } catch { /* */ }
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

// While papers of this round are still extracting, "Finalize" would bundle an incomplete
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
  const addBtn = PROJECT
    ? `<a class="doctab doctab-add" href="/extract?dataset=${esc(PROJECT)}" title="Add more papers to this dataset">＋ Add papers</a>`
    : "";
  const docTabs = DOCS.map((d) => {
    const nm = d.filename || d.title || "untitled";
    return `<button class="doctab${d.document_id === DOCID ? " active" : ""}" data-id="${d.document_id}" title="${esc(nm)}">`
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
  return `<div class="docsub">` + item("paper", "📄 Paper", "")
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
function renderPaperPanel(panel) {
  const p = DATA.paper || {};
  const pm = DATA.paper_metadata || {};
  const hasIdent = p.title || (Array.isArray(p.authors) ? p.authors.length : p.authors) || p.year || p.journal || p.doi;
  const hasConst = Object.keys(CONSTANT).length > 0;
  const paperFields = VM.paper.fields;
  const extraKeys = VM.legacy ? [] : undeclared(VM, "paper", pm).filter((k) => typeof pm[k] !== "object" || pm[k] === null);
  const orphans = orphanEvidence();
  if (!hasIdent && !hasConst && !paperFields.length && !extraKeys.length && !orphans.length) return;
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
    wireControls(pf, savePaperField, true);
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

// A study-constant edit writes to EVERY record (one call) + syncs in-memory records/CONSTANT.
function saveStudyField(key, value) {
  return api.setDocumentField(DATA.document_id, key, value)
    .then(() => {
      (DATA.records || []).forEach((r) => { if (r.field_values) r.field_values[key] = value; });
      CONSTANT[key] = value;
    })
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
    + (chainSpec() && !GRID && !RAW ? `<button class="btn btn-ghost" id="chaintoggle" title="${CHAIN_OFF ? "the claims as cause → effect chains with their quotes and results" : "every field, editable"}">${CHAIN_OFF ? "⛓ Chain" : "▤ Fields"}</button>` : "")
    + `<button class="btn btn-ghost" id="gridtoggle" title="spreadsheet view of all records">${GRID ? "▤ Cards" : "▦ Grid"}</button>`
    + `<button class="btn btn-ghost" id="rawtoggle">${RAW ? "◫ Rendered" : "{ } Raw"}</button>`
    + `<span class="jobwait" id="jobwait" hidden><span class="spin"></span> <span id="jobwait-txt">Extracting…</span></span>`
    + (PROJECT   // already a dataset: Finalize simply opens its overview (audit report, dashboards, export, publishing)
        ? `<a class="btn btn-primary" id="dlsave" href="/dataset?id=${encodeURIComponent(PROJECT)}" title="the dataset overview: audit report, dashboards, export and publishing">✓ Finalize</a>`
        : `<button class="btn btn-primary" id="dlsave" title="finish the review: overview with the audit report, exports, and the option to save">✓ Finalize</button>`)
    + `<button class="btn btn-ghost" id="dljson">⬇ JSON</button>`
    + `<button class="btn btn-ghost" id="dlcsv">⬇ CSV</button>`
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
  const recs = DATA.records;
  if (recs.length > 1) {                // many entries (e.g. one per table) → view one at a time
    if (PANEL_SEL === null) PANEL_SEL = TRIAGE === "order" ? "paper" : orderedEntries(VM, EV, recs, TRIAGE, DATA.issues)[0].i;
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
  nav.innerHTML = `<button class="rnav-tab${PANEL_SEL === "paper" ? " active" : ""}" data-sel="paper">📄 Paper</button>`
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
    + `<div class="verify"><button class="vbtn ok" data-status="verified">✓ verify</button>`
    + `<button class="vbtn flag" data-status="flagged">⚑ flag</button></div>`
    + (myIssues.length ? `<div class="issue-note" title="${esc(myIssues.map((i) => `${i.path}: ${i.message}`).join("\n"))}">⚠ ${myIssues.length} structural issue${myIssues.length === 1 ? "" : "s"} in the model output — hover for details</div>` : "")
    + (VM.legacy ? renderLegacyConf(rec) + renderLegacyBody(rec) : renderEntryBody(rec))
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

// The declared layout: tabs → (group badges → fields → sub-entries) per tab, an "Other"
// tab for anything undeclared the model returned. Every declared field appears, a missing
// one visibly so.
function renderEntryBody(rec) {
  const fv = rec.field_values || {};
  const eopts = { editable: true };
  const tabs = VM.tabs.map((t) => {
    const defs = t.fields.map((n) => VM.fieldIndex.get(n)).filter((x) => x && x.scope === "entry").map((x) => x.field);
    const children = t.fields.map((n) => VM.fieldIndex.get(n)).filter((x) => x && x.scope === "child_key").map((x) => x.child);
    // child-scope groups are rated per sub-entry and shown on each card, not on the tab
    const badges = (t.groups || []).filter((gid) => (VM.groups[gid] || {}).scope !== "child")
      .map((gid) => renderConfBadge(gid, VM.groups[gid], (rec.confidence || {})[gid], VM.levels)).join("");
    let html = badges ? `<div class="group-head">${badges}</div>` : "";
    if (defs.length) html += `<div class="rv-root">${renderFields(defs, fv, "", eopts)}</div>`;
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
async function doAddFinding() {
  const template = VM.legacy ? {} : seedEntry(VM);
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
  if (!ids.length) { alert("Nothing to finalize yet."); return; }
  const screened = ids.filter((id) => !((nrecOf.get(id) || 0) > 0)).length;
  if (screened && !confirm(`${screened} of ${ids.length} paper(s) have no extracted records.\n`
      + `They'll be kept as "screened — no records" so the dataset records that they were attempted. Continue?`)) return;
  b.disabled = true;
  try {
    // record the recipe (schema/preset + model + the exact prompt) so re-opening the
    // dataset can add papers with the same preset without re-choosing it.
    const model = (DATA.records || []).map((r) => r.extraction && r.extraction.model).find(Boolean) || null;
    const recipe = { schema_id: DATA.schema_id || null, model };
    // no dialog here: the papers are bundled and the overview opens; naming and keeping it happen there
    const base = ids.length > 1 ? `${ids.length} papers` : ((DATA.paper && DATA.paper.title) || (DATA.filename || "").replace(/\.pdf$/i, ""));
    const ds = await saveToWorkspace(ids, { defaultName: base, recipe, silent: true });
    if (ds) {
      if (ds.failed) alert(`${ds.failed} paper(s) could not be included; the rest are in the overview.`);
      location.href = `/dataset?id=${encodeURIComponent(ds.id)}&finalized=1`;   // overview: audit report, export, save
    } else b.disabled = false;
  } catch (e) { alert("finalize failed: " + e.message); b.disabled = false; }
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
  wireControls(card, (path, val) => saveFieldEdit(rec, card, path, val, curVal(rec, path)), true);
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
    const card = (chain && document.querySelector(".chain-card.cur[data-rid]")) || document.querySelector(".record[data-rid]");
    if (chain && (e.key === "j" || e.key === "]" || e.key === "k" || e.key === "[")) { chainStep(e.key === "j" || e.key === "]" ? 1 : -1); e.preventDefault(); }
    else if (e.key === "j" || e.key === "]") { nextEntry(false, 1); e.preventDefault(); }
    else if (e.key === "k" || e.key === "[") { nextEntry(false, -1); e.preventDefault(); }
    else if (e.key === "n") { nextEntry(true, 1); e.preventDefault(); }
    else if (e.key === "v" && card) { const b = card.querySelector('.vbtn[data-status="verified"]'); if (b) b.click(); }
    else if (e.key === "f" && card) { const b = card.querySelector('.vbtn[data-status="flagged"]'); if (b) b.click(); }
    else if (/^[1-9]$/.test(e.key) && card) { const b = card.querySelectorAll(".subtab")[+e.key - 1]; if (b) b.click(); }
    else if (e.key === "?") {
      const old = $(".kbd-help"); if (old) { old.remove(); return; }
      const h = document.createElement("div"); h.className = "kbd-help";
      h.innerHTML = `<b>Keys</b> · <kbd>j</kbd>/<kbd>k</kbd> next/prev entry · <kbd>n</kbd> next unreviewed · <kbd>v</kbd> verify · <kbd>f</kbd> flag · <kbd>1</kbd>–<kbd>9</kbd> tab · <kbd>?</kbd> close`
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
  for (const rec of recs) renderChainCard(panel, rec, rv);
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
        + ` <span class="chain-num">${val === null || val === undefined ? "—" : esc(String(val))}${se !== null && se !== undefined ? ` (${esc(String(se))})` : ""}</span> ${stratum}${chk}${signOpp}${role}</div>`
        + (R.row && row[R.row] ? `<div class="chain-rowlab muted">${esc(row[R.row])}</div>` : "")
        + (R.cause ? `<div class="chain-op"><span class="k">cause</span><span>${esc(row[R.cause] || "—")} ${rel("cause")}</span></div>` : "")
        + (R.effect ? `<div class="chain-op"><span class="k">effect</span><span>${esc(row[R.effect] || "—")} ${rel("effect")}</span></div>` : "")
        + (R.why && row[R.why] ? `<details class="chain-why"><summary class="muted">why this result</summary><div class="muted">${esc(row[R.why])}</div></details>` : "")
        + `</div>`;
    });
    if (!rows.length) body = `<span class="chain-none muted">${isEdge ? (fv[rv.notes] ? `no extracted result carries this claim — ${esc(fv[rv.notes])}` : "no result mapped") : "—"}</span>`;
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
    + `<span style="margin-left:auto"></span><button class="histbtn" title="change history">↻ history</button><button class="recdel" title="delete this ${esc(VM.entries.label.toLowerCase())}">🗑</button></div>`
    + edge + (scope.length ? `<div class="chain-scope muted">${scope.map(esc).join(" · ")}</div>` : "")
    + `<dl class="chain-dl">${dl}</dl>`
    + `<div class="chain-foot"><button class="vbtn ok${rec.verification_status === "verified" ? " on" : ""}" data-status="verified">OK</button>`
    + `<button class="vbtn flag${rec.verification_status === "flagged" ? " on" : ""}" data-status="flagged">Flag</button>`
    + chainReviewNote(rec)
    + (label ? `<span class="tag chain-sup${support === "weak" ? " warn" : ""}">${esc(label)}</span>` : "") + `</div><div class="histbody" hidden></div>`;
  // wiring: history, delete, verify/flag, the quotes' jumps, the edge focus
  card.querySelector(".histbtn").onclick = () => toggleHistory(card, rec);
  card.querySelector(".recdel").onclick = () => doDeleteRecord(rec);
  card.querySelectorAll(".vbtn").forEach((b) => (b.onclick = async () => {
    await sendVerify(card, rec, b.dataset.status, chainNoteText(card));
    card.querySelectorAll(".vbtn").forEach((x) => x.classList.toggle("on", x.dataset.status === rec.verification_status));
    card.querySelector(".chain-review").hidden = rec.verification_status !== "flagged" && !rec.note;
  }));
  wireChainNote(card, rec);
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
  return `<span class="chain-review"${open ? "" : " hidden"}>`
    + `<select class="chain-reason">${CHAIN_REASONS.map(([v, t]) => `<option value="${esc(t)}"${t === reason ? " selected" : ""}${v ? "" : ' data-empty="1"'}>${esc(t)}</option>`).join("")}</select>`
    + `<input class="chain-notein" placeholder="note" value="${esc(text)}"/></span>`;
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
  const save = async () => {
    const status = rec.verification_status === "unverified" ? "flagged" : rec.verification_status;
    await sendVerify(card, rec, status, chainNoteText(card));
    card.querySelectorAll(".vbtn").forEach((x) => x.classList.toggle("on", x.dataset.status === rec.verification_status));
  };
  box.querySelector(".chain-reason").onchange = save;
  box.querySelector(".chain-notein").onchange = save;
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

// Persist ONE field correction: apply newVal at `path` on a copy of the entry's
// field_values and route it through the verify layer — so the change lands in the record
// (→ export + reload) and the audit trail, not just the UI. Shared by text cells + typed
// controls. Returns the api.verify promise.
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
  const fv = JSON.parse(JSON.stringify(rec.field_values || {}));
  setByPath(fv, path, newVal);
  // A value edit is a CORRECTION (logged for provenance/audit), NOT a verification —
  // it must not mark the record "verified"; that stays the explicit ✓ button's job.
  return api.verify(rec.id, {
    status: "corrected",
    diff: [{ field_path: path, original_value: origVal, final_value: newVal }],
    field_values: fv,
  }).then(() => { rec.field_values = fv; maybeOfferRepublish(); })
    .catch((e) => alert("save failed: " + e.message));
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
    mbox.querySelectorAll('input[type="checkbox"]').forEach((cb) => (cb.onchange = () => {
      mbox.classList.add("rv-edited");
      const vals = [...mbox.querySelectorAll('input[type="checkbox"]:checked')].map((x) => x.value);
      onSave(path, vals);
    }));
  });
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
  const out = {
    schema_id: DATA.schema_id, paper: DATA.paper, paper_metadata: DATA.paper_metadata,
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
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const q = (v) => { if (v == null) return ""; const s = String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => q(r[c])).join(","))].join("\n");
  download(`${baseName()}.csv`, csv, "text/csv");
}

init();
