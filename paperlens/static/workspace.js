// Workspace — PDF pages + highlight overlays alongside the extracted records,
// with click-to-source, verify/flag, and edit-in-place (corrections route to
// the verification layer). Uses the shared grammar + pdfview modules.
import { api } from "/static/api.js";
import { renderValue, renderConfidence, esc } from "/static/grammar.js";
import { renderPages, jumpToEvidence, showEvidence, hideEvidence, flashRects } from "/static/pdfview.js";
import { saveToWorkspace } from "/static/save.js";
import { renderGrid } from "/static/gridview.js";

const $ = (s, el = document) => el.querySelector(s);
let DATA = null, DOCS = [], DOCID = null, RAW = false, GRID = false, PROJECT = null, PROJECT_TITLE = "", FOCUS_REC = null;
let JOBS = {};   // job_id -> {status:'pending'|'complete'|'failed', document_id?, error?} — this-round tracking

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
    const w = Math.max(300, Math.min(1100, e.clientX - panel.getBoundingClientRect().left));
    panel.style.width = w + "px";
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false; sp.classList.remove("drag"); document.body.style.userSelect = "";
    localStorage.setItem("metalens_panel_w", String(parseInt(panel.style.width, 10) || 480));
  });
}

async function init() {
  mountSplitter();
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
  // (Pick a specific dataset to review from My Workspace instead of a chooser here.)
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
function startJobTracking(jobIds, since) {
  jobIds.forEach((id) => (JOBS[id] = { status: "pending" }));
  renderDocTabs();
  let ticks = 0;
  const timer = setInterval(async () => {
    let pending = false, newDoc = false;
    for (const id of jobIds) {
      const j = JOBS[id];
      if (!j || j.status === "complete" || j.status === "failed") continue;
      let st;
      try { st = await api.job(id); } catch { pending = true; continue; }
      if (st.success === true) { j.status = "complete"; j.document_id = st.result && st.result.document_id; newDoc = true; }
      else if (st.success === false) { j.status = "failed"; j.error = st.error || "extraction failed"; }
      else pending = true;
    }
    if (newDoc) await reloadRoundDocs(since);
    renderDocTabs();
    if (!pending || ++ticks > 45) clearInterval(timer);   // done, or ~6 min safety cap
  }, 8000);                                               // 8s poll (was 4s) — halves Upstash job-status polling
}

// Data-review entry: if the user has datasets, let them pick which to review; if
// they have only loose extractions, fall through and load them; if nothing, extract.
async function landingChooser() {
  let mine = [], docs = [];
  try {
    const me = await api.me();
    const all = (await api.myDatasets()).datasets || [];
    mine = (me && me.id) ? all.filter((d) => d.owner_user_id === me.id) : [];
    docs = (await api.documents({})).documents || [];
  } catch { return false; }
  if (!mine.length && !docs.length) { location.href = "/extract"; return true; }
  if (!mine.length) return false;                       // loose docs only → load them
  const dsTiles = mine.map((d) =>
    `<a class="rv-ds" href="/workspace?project=${esc(d.id)}"><div class="rv-ds-t">${esc(d.title || "untitled")}</div>`
    + `<div class="rv-ds-m">${d.n_records} record${d.n_records === 1 ? "" : "s"} · ${esc(d.visibility)}</div></a>`).join("");
  const loose = docs.length
    ? `<a class="rv-ds" href="/workspace?doc=${esc(docs[0].document_id)}"><div class="rv-ds-t">All my papers</div>`
      + `<div class="rv-ds-m">${docs.length} document${docs.length === 1 ? "" : "s"}</div></a>` : "";
  $("#doctabs").innerHTML = ""; $("#panelbody").innerHTML = "";
  $("#pages").innerHTML = `<div class="rv-choose"><h3 style="margin:0 0 12px">Choose a dataset to review</h3>`
    + `<div class="rv-ds-grid">${dsTiles}${loose}</div>`
    + `<p class="muted" style="margin-top:16px"><a href="/extract">＋ Extract new papers</a></p></div>`;
  return true;
}

// paper switcher — a tab per document, above the records
function renderDocTabs() {
  const strip = $("#doctabs"); if (!strip) return;
  const label = PROJECT_TITLE ? `<span class="doctabs-label">${esc(PROJECT_TITLE)}</span>` : "";
  const docTabs = DOCS.map((d) => {
    const nm = d.filename || d.title || "untitled";
    return `<button class="doctab${d.document_id === DOCID ? " active" : ""}" data-id="${d.document_id}" title="${esc(nm)}">`
      + `${esc(nm.slice(0, 30))}<span class="dt-n">${d.n_records}</span></button>`;
  }).join("");
  // still-running or failed papers from this round that aren't yet a visible document
  const shown = new Set(DOCS.map((d) => d.document_id));
  const jobTabs = Object.values(JOBS).filter((j) =>
    j.status === "pending" || j.status === "failed"
    || (j.status === "complete" && j.document_id && !shown.has(j.document_id))
  ).map((j) => j.status === "failed"
    ? `<span class="doctab jobfail" title="${esc(j.error || "extraction failed")}">✗ failed</span>`
    : `<span class="doctab jobpend">⏳ extracting…</span>`).join("");
  strip.innerHTML = label + docTabs + jobTabs;
  strip.querySelectorAll(".doctab[data-id]").forEach((b) => (b.onclick = () => selectDoc(b.dataset.id)));
}

async function load(docId) {
  DOCID = docId;
  $("#pages").innerHTML = '<p class="muted">Loading…</p>';
  DATA = await api.documentView(docId);
  // "Screened — no records" sentinels aren't data rows: drop them so the review shows the
  // screened empty-state (not an empty card), but remember the doc WAS screened.
  DATA._screened = (DATA.records || []).some((r) => r.screened_empty);
  DATA.records = (DATA.records || []).filter((r) => !r.screened_empty);
  renderPages($("#pages"), DATA.pages, DATA.evidence);
  renderPanel();
  if (FOCUS_REC) { focusRecord(FOCUS_REC); FOCUS_REC = null; }   // one-shot deep-link focus
}

// Scroll to a specific record's card, pulse it, and flash its first evidence (from a
// chart deep-link, /workspace?doc=&rec=).
function focusRecord(rec) {
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
// e.g. a masem `field:"records"` with no samples[i] prefix). Used as a fallback so
// value cells still link to *some* source page instead of being dead on click.
function orphanEvidence() {
  return DATA.evidence
    .map((ev, i) => ({ ev, i }))
    .filter(({ ev }) => ev.record_id == null && (ev.entry_index === null || ev.entry_index === undefined));
}

// Fields whose value is IDENTICAL across every record → shown once in the study panel
// (with >1 record only; a single record keeps everything in the entry). Recomputed per
// document load. CONSTANT maps key → shared value; entry views drop these keys.
let CONSTANT = {};
function computeConstant() {
  CONSTANT = {};
  const recs = DATA.records || [];
  if (recs.length <= 1) return;
  const skip = new Set(["evidence", "extraction_confidence"]);
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

// Group an extracted record into the schema's sub_views as tabs, when the preset
// defines them; else render flat. Constant-across-entries keys are dropped (they live
// in the study panel).
function _subViews() {
  const sv = DATA.field_defs && DATA.field_defs.sub_views;
  return Array.isArray(sv) && sv.length > 1 ? sv : null;
}
function entryFields(fv) {
  const out = {};
  for (const [k, v] of Object.entries(fv || {})) {
    if (k === "evidence" || k === "extraction_confidence" || _isConstant(k)) continue;
    out[k] = v;
  }
  return out;
}
function _fieldsForView(fv, view) {
  const skip = new Set(["evidence", "extraction_confidence"]);
  const inc = Array.isArray(view.include_keys) && view.include_keys.length ? new Set(view.include_keys) : null;
  const exc = new Set(view.exclude_keys || []);
  const out = {};
  for (const [k, v] of Object.entries(fv || {})) {
    if (skip.has(k) || _isConstant(k)) continue;   // constant → study panel, not the entry
    if (inc ? inc.has(k) : !exc.has(k)) out[k] = v;
  }
  return out;
}
// per-field control metadata (dropdown/multi-select options) declared by the preset
function fieldTypes() { return (DATA.field_defs && DATA.field_defs.field_types) || {}; }

function renderRecordBody(rec) {
  const eopts = { editable: true, fieldTypes: fieldTypes() };
  const views = _subViews();
  if (!views) return renderValue(entryFields(rec.field_values), eopts);
  // only show tabs that actually have fields for this record
  const present = views.filter((v) => Object.keys(_fieldsForView(rec.field_values, v)).length);
  if (present.length < 2) return renderValue(entryFields(rec.field_values), eopts);
  const tabs = present.map((v, i) =>
    `<button class="subtab${i === 0 ? " active" : ""}" data-vi="${i}">${esc(v.label || v.id)}</button>`).join("");
  const panels = present.map((v, i) =>
    `<div class="subpanel${i === 0 ? "" : " hidden"}" data-vi="${i}">${renderValue(_fieldsForView(rec.field_values, v), eopts)}</div>`).join("");
  return `<div class="subtabs">${tabs}</div><div class="subpanels">${panels}</div>`;
}

// Study panel shown ONCE above the entries: (a) paper identity (editable → the paper
// record) + (b) any field identical across all entries (editable → propagates to every
// entry). Single entry → identity only.
function renderStudyBlock(panel) {
  const p = DATA.paper || {};
  const hasIdent = p.title || (Array.isArray(p.authors) ? p.authors.length : p.authors) || p.year || p.journal || p.doi;
  const hasConst = Object.keys(CONSTANT).length > 0;
  if (!hasIdent && !hasConst) return;
  const box = document.createElement("details");
  box.className = "study-block"; box.open = true;
  // identity — Title/Year/Venue/Authors editable (persist to the paper record); DOI read-only
  const identRows = [
    identRow("Title", "title", p.title, false),
    identRow("Authors", "authors", Array.isArray(p.authors) ? p.authors.join("; ") : (p.authors || ""), false),
    identRow("Year", "year", p.year, true),
    identRow("Venue", "journal", p.journal, false),
    p.doi ? `<div class="rv-row"><div class="rv-key">DOI</div><div class="rv-val"><span class="rv-cell">${esc(p.doi)}</span></div></div>` : "",
  ].join("");
  let body = `<div class="rv-root"><div class="rv-obj">${identRows}</div></div>`;
  if (hasConst) {
    body += `<div class="study-shared"><div class="study-sub">Shared across all ${(DATA.records || []).length} entries</div>`
      + `${renderValue(CONSTANT, { editable: true, fieldTypes: fieldTypes() })}</div>`;
  }
  box.innerHTML = `<summary>📄 Study information</summary><div class="study-body">${body}</div>`;
  panel.appendChild(box);
  wireStudyIdentity(box);
  const shared = box.querySelector(".study-shared");
  if (shared) wireControls(shared, saveStudyField, true);   // a constant edit → every entry
  // constant fields carry the same evidence on every record → link them to their source
  linkValueCells(box, DATA.evidence.map((ev, i) => ({ ev, i })));
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

// A study-constant edit writes to EVERY record (one call) + syncs in-memory records/CONSTANT.
function saveStudyField(key, value) {
  return api.setDocumentField(DATA.document_id, key, value)
    .then(() => {
      (DATA.records || []).forEach((r) => { if (r.field_values) r.field_values[key] = value; });
      CONSTANT[key] = value;
    })
    .catch((e) => alert("save failed: " + e.message));
}

function renderPanel() {
  computeConstant();
  const panel = $("#panelbody");
  panel.innerHTML = `<div class="panelhead"><span><b>${DATA.records.length}</b> records · `
    + `<code>${esc(DATA.schema_id || "—")}</code></span>`
    + `<span class="dlbtns">`
    + `<button class="btn btn-ghost" id="gridtoggle" title="spreadsheet view of all records">${GRID ? "▤ Cards" : "▦ Grid"}</button>`
    + `<button class="btn btn-ghost" id="rawtoggle">${RAW ? "◫ Rendered" : "{ } Raw"}</button>`
    + (PROJECT ? "" : `<button class="btn btn-primary" id="dlsave">💾 Save all</button>`)
    + `<button class="btn btn-ghost" id="dljson">⬇ JSON</button>`
    + `<button class="btn btn-ghost" id="dlcsv">⬇ CSV</button>`
    + `<button class="btn btn-ghost" id="addfinding" title="add a manual finding">＋ Finding</button>`
    + `<button class="btn btn-ghost" id="deldoc" title="delete this document + its PDF/pages">🗑</button></span></div>`;
  renderStudyBlock(panel);              // study-level info once, above the entries
  if (!DATA.records.length) {           // 0-record doc: clear empty state; Raw shows the raw output
    const box = document.createElement("div");
    box.className = "empty-records";
    if (RAW) {
      box.innerHTML = `<div class="rectitle">raw extraction output</div>`
        + `<pre class="rawjson">${esc(JSON.stringify({ paper_metadata: DATA.paper_metadata, evidence: DATA.evidence }, null, 2))}</pre>`;
    } else {
      const screened = DATA._screened
        ? `<p class="muted" style="padding:0 2px 8px">✓ Recorded in the dataset as <b>screened — no applicable records</b>.</p>` : "";
      box.innerHTML = screened
        + `<p class="muted" style="padding:8px 2px 12px">No records were extracted from this document — nothing matched the `
        + `<code>${esc(DATA.schema_id || "")}</code> preset (this paper may not contain the kind of data it targets). `
        + `You can still enter the data by hand, or re-process with a different preset.</p>`
        + `<button class="btn btn-primary" id="empty-add">＋ Add a finding manually</button>`;
    }
    panel.appendChild(box);
    wirePanelHead();
    const ea = $("#empty-add"); if (ea) ea.onclick = doAddFinding;   // seed a blank editable entry
    return;
  }
  if (GRID) { renderGridInto(panel); wirePanelHead(); return; }
  DATA.records.forEach((rec) => {
    const card = document.createElement("div");
    card.className = "record"; card.dataset.rid = rec.id;
    if (RAW) {
      card.innerHTML = `<div class="rectitle">entry ${rec.entry_index}`
        + `<span class="status ${rec.verification_status}">${rec.verification_status}</span></div>`
        + `<pre class="rawjson">${esc(JSON.stringify(rec.field_values, null, 2))}</pre>`;
      panel.appendChild(card);
      return;
    }
    const evs = recordEvidence(rec);
    card.innerHTML =
      `<div class="rectitle">entry ${rec.entry_index}`
      + `<span class="status ${rec.verification_status}">${rec.verification_status}</span>`
      + `<button class="histbtn" title="change history">↻ history</button>`
      + `<button class="recdel" title="delete this finding">🗑</button></div>`
      + `<div class="verify"><button class="vbtn ok" data-status="verified">✓ verify</button>`
      + `<button class="vbtn flag" data-status="flagged">⚑ flag</button></div>`
      + renderConfidence(rec.field_values && rec.field_values.extraction_confidence)
      + renderRecordBody(rec)
      + `<div class="chips">${evs.map(({ ev, i }) =>
          `<button class="chip" data-eid="${i}" data-page="${ev.page}">p${ev.page} · ${esc((ev.snippet || "").slice(0, 40))}</button>`
        ).join("") || '<span class="muted">no evidence</span>'}</div>`
      + `<div class="histbody" hidden></div>`;
    wireCard(card, rec);
    panel.appendChild(card);
  });
  wirePanelHead();
}

function wirePanelHead() {
  $("#dljson").onclick = downloadJSON;
  $("#dlcsv").onclick = downloadCSV;
  const save = $("#dlsave"); if (save) save.onclick = doSave;
  $("#addfinding").onclick = doAddFinding;
  $("#deldoc").onclick = doDelete;
  $("#rawtoggle").onclick = () => { RAW = !RAW; renderPanel(); };
  const g = $("#gridtoggle"); if (g) g.onclick = () => { GRID = !GRID; renderPanel(); };
}

// Cross-record spreadsheet of the current document's records. Rendered in the
// records panel so the PDF stays on the left — clicking a linked cell still jumps
// to (and pinpoint-highlights) its source, exactly like a card value cell.
function renderGridInto(panel) {
  const host = document.createElement("div");
  panel.appendChild(host);
  renderGrid(host, {
    records: DATA.records,
    subViews: (DATA.field_defs && DATA.field_defs.sub_views) || [],
    evidenceFor: (rec) => recordEvidence(rec).concat(orphanEvidence())
      .map(({ ev, i }) => ({ i, page: ev.page, path: stripCore(ev.field_path) })),
    onCellClick: (rec, col, cov, e) => { if (cov) gridCellClick(e.currentTarget, cov); },
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
  if (!confirm(`Delete finding (entry ${rec.entry_index})? This cannot be undone.`)) return;
  try { await api.deleteRecord(rec.id); await reloadDoc(); }
  catch (e) { alert("delete failed: " + e.message); }
}

// Add a manual finding: seed it with the current schema's top-level keys (blanked)
// so its cells are immediately editable; edits route through the verify layer.
async function doAddFinding() {
  const template = {};
  // Seed a COMPLETE blank form from the preset's entry setup: every field named across
  // its sub-views, so a manual finding starts as a full blank form even on a fresh doc.
  const views = (DATA.field_defs && DATA.field_defs.sub_views) || [];
  views.forEach((v) => (v.include_keys || []).forEach((k) => { if (!(k in template)) template[k] = ""; }));
  // Union with keys any existing record actually carries (covers presets without
  // sub_views, and fields outside include_keys), preserving nested shape as empty.
  (DATA.records || []).forEach((r) => {
    for (const [k, v] of Object.entries(r.field_values || {})) {
      if (k === "evidence" || k === "extraction_confidence" || (k in template)) continue;
      template[k] = (v && typeof v === "object") ? (Array.isArray(v) ? [] : {}) : "";
    }
  });
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
      + `They'll be saved as "screened — no records" so the dataset records that they were attempted. Continue?`)) return;
  b.disabled = true;
  try {
    // record the recipe (schema/preset + model) so re-opening the dataset can add
    // papers with the same preset without re-choosing it.
    const model = (DATA.records || []).map((r) => r.extraction && r.extraction.model).find(Boolean) || null;
    const recipe = { schema_id: DATA.schema_id || null, model };
    const ds = await saveToWorkspace(ids, { defaultName: (DATA.paper && DATA.paper.title) || "", recipe });
    if (ds) {
      const note = ds.failed ? ` · ${ds.failed} failed` : "";
      b.textContent = `✓ saved ${ds.saved != null ? ds.saved : ids.length}${note} (${ds.visibility})`;
      setTimeout(() => { b.textContent = "💾 Save all"; b.disabled = false; }, 3000);
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
  card.querySelectorAll(".chip").forEach((b) => {
    const eid = +b.dataset.eid, page = +b.dataset.page;
    b.onclick = () => jumpToEvidence(page, eid);
    b.onmouseenter = () => showEvidence(eid);
    b.onmouseleave = () => hideEvidence(eid);
  });
  linkValueCells(card, recordEvidence(rec).concat(orphanEvidence()));
  card.querySelectorAll(".vbtn").forEach((b) =>
    (b.onclick = () => sendVerify(card, rec, b.dataset.status)));
  // free-text/number cells + typed dropdown & multi-select controls → the verify layer
  wireControls(card, (path, val) => saveFieldEdit(rec, card, path, val, curVal(rec, path)), true);
}

async function sendVerify(card, rec, status) {
  card.querySelectorAll(".vbtn").forEach((b) => (b.disabled = true));
  try { await api.verify(rec.id, { status }); setStatus(card, status); }
  catch (e) { alert("verify failed: " + e.message); }
  finally { card.querySelectorAll(".vbtn").forEach((b) => (b.disabled = false)); }
}

function setStatus(card, status) {
  const badge = card.querySelector(".status");
  badge.textContent = status; badge.className = `status ${status}`;
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
  return `<div class="hist-row"><span class="hist-meta">${esc(e.status)} · ${esc(who)} · ${esc(when)}</span>${changes}</div>`;
}

// ── value ↔ evidence linking ────────────────────────────────────────────────
// evidence.field_path is full ("records[0].estimates._table[0].coefficient");
// grammar value cells carry a record-relative data-path. Strip the core prefix.
function stripCore(fp) {
  if (!fp) return "";
  return fp.replace(/^[a-zA-Z_][a-zA-Z0-9_]*\[\d+\]\.?/, "");
}

// Set a value at a record-relative data-path — "a", "a.b", "a[0].b", "a._table[0].c" —
// creating intermediate objects/arrays as needed. Inverse of the paths grammar.js emits.
function setByPath(obj, path, value) {
  const toks = [];
  String(path).replace(/[^.[\]]+|\[(\d+)\]/g, (m, idx) => (toks.push(idx !== undefined ? Number(idx) : m), ""));
  if (!toks.length) return;
  let cur = obj;
  for (let k = 0; k < toks.length - 1; k++) {
    const key = toks[k];
    if (cur[key] == null || typeof cur[key] !== "object") cur[key] = typeof toks[k + 1] === "number" ? [] : {};
    cur = cur[key];
  }
  cur[toks[toks.length - 1]] = value;
}

// Persist ONE field correction: apply newVal at `path` on a copy of the entry's
// field_values and route it through the verify layer — so the change lands in the record
// (→ export + reload) and the audit trail, not just the UI. Shared by text cells + typed
// controls. Returns the api.verify promise.
function saveFieldEdit(rec, card, path, newVal, origVal) {
  const fv = JSON.parse(JSON.stringify(rec.field_values || {}));
  setByPath(fv, path, newVal);
  return api.verify(rec.id, {
    status: "verified",
    diff: [{ field_path: path, original_value: origVal, final_value: newVal }],
    field_values: fv,
  }).then(() => { rec.field_values = fv; setStatus(card, "verified"); })
    .catch((e) => alert("save failed: " + e.message));
}
// current stored value at a (top-level) field path — used as the diff's original_value
function curVal(rec, path) { return (rec.field_values || {})[path]; }

// Wire the typed controls (+ free-text cells when textToo) inside a container to
// onSave(path, value). Shared by record cards (per-record save) and the study panel
// (propagate-to-all save). Handles select, allow_other, and multi-select (array value).
function wireControls(container, onSave, textToo) {
  if (textToo) container.querySelectorAll(".rv-editable").forEach((cell) => {
    if (cell.classList.contains("study-ident")) return;   // identity has its own handler
    cell.dataset.orig = cell.textContent;
    cell.addEventListener("blur", () => {
      const now = cell.textContent;
      if (now === cell.dataset.orig) return;
      cell.classList.add("rv-edited");
      const val = cell.classList.contains("rv-num") && now.trim() !== "" && !isNaN(Number(now)) ? Number(now) : now;
      Promise.resolve(onSave(cell.dataset.path, val)).then(() => (cell.dataset.orig = now));
    });
  });
  container.querySelectorAll(".rv-selwrap").forEach((wrap) => {
    const path = wrap.dataset.path, sel = wrap.querySelector(".rv-select"), other = wrap.querySelector(".rv-other");
    const commit = (val) => { wrap.classList.add("rv-edited"); onSave(path, val); };
    sel.onchange = () => {
      if (sel.value === "__other__") { if (other) { other.hidden = false; other.focus(); } return; }
      if (other) other.hidden = true;
      commit(sel.value || null);
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
      // read-only cells get the "link" affordance; editable cells keep the text cursor
      // + dashed underline so they still read as editable.
      if (!editable) cell.classList.add("rv-linked");
      // TEXT → jump to the cited snippet; NUMBER → verbatim-locate on the evidence page.
      // A dropdown/checkbox control: clicking operates it, so no click-jump (hover still
      // previews the source).
      if (!isControl) cell.addEventListener("click", () => verifyAndJump(cell, best));
      return;
    }
    // No cited evidence. Verbatim value-search is NUMERIC-only, so only numbers stay
    // clickable-to-locate; a text value with no cited snippet has nothing to jump to.
    if (cell.classList.contains("rv-num")) {
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
async function verifyAndJump(cell, best) {
  if (cell.nextElementSibling && cell.nextElementSibling.classList.contains("val-check"))
    cell.nextElementSibling.remove();
  const txt = cell.textContent.trim();
  if (!NUM_RE.test(txt)) { jumpToEvidence(best.page, best.i); return; }   // (a) text → snippet
  const num = txt.replace(/%$/, "");                 // keep commas; server tries both forms
  try {
    const r = await api.locateValue(DATA.document_id, num, best.page);    // (b) number → the page's number
    if (r.no_pdf) { jumpToEvidence(best.page, best.i); return; }
    if (r.found) { flashRects(r.page || best.page, r.rects); return; }    // highlight wherever it actually is
  } catch { jumpToEvidence(best.page, best.i); return; }
  jumpToEvidence(best.page, best.i);                 // number not verbatim → snippet + soft note
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
    schema_id: DATA.schema_id, paper: DATA.paper,
    records: DATA.records.map((r) => r.field_values),
    // provenance parallel to records[]: review status + any human corrections (original→final,
    // who, when) so a consumer can tell model-extracted values from human-corrected ones.
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
      if (k === "evidence" || k === "extraction_confidence") return;
      const key = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === "object" && !Array.isArray(v) && !("_table" in v)) walk(v, key);
      else out[key] = v && typeof v === "object" ? JSON.stringify(v) : v;
    });
  })(fv, "");
  return out;
}
function downloadCSV() {
  const rows = DATA.records.map((r) => {
    const flat = flattenRecord(r.field_values);
    flat.verification_status = r.verification_status || "";        // provenance columns, appended last
    flat.corrected_fields = (r.corrections || []).map((c) => c.field_path).join("; ");
    return flat;
  });
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const q = (v) => { if (v == null) return ""; const s = String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => q(r[c])).join(","))].join("\n");
  download(`${baseName()}.csv`, csv, "text/csv");
}

init();
