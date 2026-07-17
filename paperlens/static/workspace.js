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
    else if (++idle >= 10) clearInterval(timer);   // ~40s with no new papers
  }, 4000);
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
    if (!pending || ++ticks > 90) clearInterval(timer);   // done, or ~6 min safety cap
  }, 4000);
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
function renderRecordBody(rec) {
  const views = _subViews();
  if (!views) return renderValue(entryFields(rec.field_values), { editable: true });
  // only show tabs that actually have fields for this record
  const present = views.filter((v) => Object.keys(_fieldsForView(rec.field_values, v)).length);
  if (present.length < 2) return renderValue(entryFields(rec.field_values), { editable: true });
  const tabs = present.map((v, i) =>
    `<button class="subtab${i === 0 ? " active" : ""}" data-vi="${i}">${esc(v.label || v.id)}</button>`).join("");
  const panels = present.map((v, i) =>
    `<div class="subpanel${i === 0 ? "" : " hidden"}" data-vi="${i}">${renderValue(_fieldsForView(rec.field_values, v), { editable: true })}</div>`).join("");
  return `<div class="subtabs">${tabs}</div><div class="subpanels">${panels}</div>`;
}

// Study panel shown ONCE above the entries: (a) identification (always) + (b) any field
// that's identical across all entries. Single entry → identification only.
function renderStudyBlock(panel) {
  const p = DATA.paper || {};
  const ident = {};
  if (p.title) ident.Title = p.title;
  if (Array.isArray(p.authors) ? p.authors.length : p.authors) ident.Authors = p.authors;
  if (p.year) ident.Year = p.year;
  if (p.journal) ident.Venue = p.journal;
  if (p.doi) ident.DOI = p.doi;
  const hasConst = Object.keys(CONSTANT).length > 0;
  if (!Object.keys(ident).length && !hasConst) return;
  const box = document.createElement("details");
  box.className = "study-block"; box.open = true;
  let body = Object.keys(ident).length ? renderValue(ident, { editable: false }) : "";
  if (hasConst) {
    body += `<div class="study-shared"><div class="study-sub">Shared across all ${(DATA.records || []).length} entries</div>`
      + `${renderValue(CONSTANT, { editable: false })}</div>`;
  }
  box.innerHTML = `<summary>📄 Study information</summary><div class="study-body">${body}</div>`;
  panel.appendChild(box);
  // constant fields carry the same evidence on every record → link them to their source
  linkValueCells(box, DATA.evidence.map((ev, i) => ({ ev, i })));
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
  const first = (DATA.records || [])[0];
  if (first && first.field_values) {
    for (const [k, v] of Object.entries(first.field_values)) {
      if (k === "evidence" || k === "extraction_confidence") continue;
      template[k] = (v && typeof v === "object") ? (Array.isArray(v) ? [] : {}) : "";
    }
  }
  try { await api.addRecord(DATA.document_id, { field_values: template }); await reloadDoc(); }
  catch (e) { alert("add failed: " + e.message); }
}

// Save ALL in-scope documents (the whole round), not just the highlighted one.
// Only offered outside a project (a project's docs are already in a dataset).
async function doSave() {
  const b = $("#dlsave");
  b.disabled = true;
  const ids = DOCS.length ? DOCS.map((d) => d.document_id) : [DATA.document_id];
  try {
    // record the recipe (schema/preset + model) so re-opening the dataset can add
    // papers with the same preset without re-choosing it.
    const model = (DATA.records || []).map((r) => r.extraction && r.extraction.model).find(Boolean) || null;
    const recipe = { schema_id: DATA.schema_id || null, model };
    const ds = await saveToWorkspace(ids, { defaultName: (DATA.paper && DATA.paper.title) || "", recipe });
    if (ds) { b.textContent = `✓ saved ${ids.length} (${ds.visibility})`; setTimeout(() => { b.textContent = "💾 Save all"; b.disabled = false; }, 3000); }
    else b.disabled = false;
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
  // edit-in-place → a value-changing correction recorded via the verify layer
  card.querySelectorAll(".rv-editable").forEach((cell) => {
    cell.dataset.orig = cell.textContent;
    cell.addEventListener("blur", () => {
      const orig = cell.dataset.orig, now = cell.textContent;
      if (now === orig) return;
      cell.classList.add("rv-edited");
      api.verify(rec.id, {
        status: "verified",
        diff: [{ field_path: cell.dataset.path, original_value: orig, final_value: now }],
      }).then(() => { cell.dataset.orig = now; setStatus(card, "verified"); })
        .catch((e) => alert("save failed: " + e.message));
    });
  });
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
function linkValueCells(card, evs) {
  const linkable = evs.map(({ ev, i }) => ({ i, page: ev.page, path: stripCore(ev.field_path) }));
  card.querySelectorAll("[data-path]").forEach((cell) => {
    const p = cell.dataset.path;
    let best = null;                                   // most specific covering evidence
    for (const x of linkable) {
      const covers = x.path === "" || x.path === p || p.startsWith(x.path + ".") || p.startsWith(x.path + "[");
      if (covers && (!best || x.path.length > best.path.length)) best = x;
    }
    if (!best) return;
    cell.classList.add("rv-linked");
    cell.addEventListener("mouseenter", () => showEvidence(best.i));
    cell.addEventListener("mouseleave", () => hideEvidence(best.i));
    cell.addEventListener("click", () => verifyAndJump(cell, best));
  });
}

// Click a value → jump to its evidence; for NUMERIC values also locate the exact
// number on the source page: found → pinpoint-highlight (green); not found verbatim
// → a soft note (may be transformed/rounded — not an error).
const NUM_RE = /^-?\d[\d,]*(\.\d+)?%?$/;
async function verifyAndJump(cell, best) {
  jumpToEvidence(best.page, best.i);
  if (cell.nextElementSibling && cell.nextElementSibling.classList.contains("val-check"))
    cell.nextElementSibling.remove();
  const txt = cell.textContent.trim();
  if (!NUM_RE.test(txt)) return;
  const num = txt.replace(/%$/, "");                 // keep commas; server tries both forms
  try {
    const r = await api.locateValue(DATA.document_id, num, best.page);
    if (r.no_pdf) return;
    if (r.found) flashRects(r.page || best.page, r.rects);   // highlight wherever it actually is
    else cell.insertAdjacentHTML("afterend",
      `<span class="val-check" title="This exact number isn't in the source PDF text — it may be rounded, transformed, or computed from other values. Not necessarily wrong.">⚠ not found verbatim</span>`);
  } catch { /* verification is best-effort */ }
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
  const out = { schema_id: DATA.schema_id, paper: DATA.paper,
                records: DATA.records.map((r) => r.field_values), evidence: DATA.evidence };
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
  const rows = DATA.records.map((r) => flattenRecord(r.field_values));
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const q = (v) => { if (v == null) return ""; const s = String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => q(r[c])).join(","))].join("\n");
  download(`${baseName()}.csv`, csv, "text/csv");
}

init();
