// Import pre-computed extractions (JSON) + their source PDFs → full viewable documents with
// highlighting, via POST /api/ingest-pdf (the extraction pipeline minus the model call), or
// POST /api/ingest when a paper has no PDF (reviewable, no page images). Accepted JSON:
//   • a single-paper result   {paper_metadata, <entries>, evidence}   (or an {extraction: …} wrapper)
//   • a workspace export      {schema_id, paper_metadata, records, confidence, provenance, evidence}
//   • a dataset export        {metadata: {schema_id, …}, results: {papers: [{filename, result}]}}
//   • a dataset file          {recipe: {schema_id}, papers: [{filename, paper, records: [{values}], evidence?}]}
//                             (the citable download of a dataset page; per-paper "evidence" is optional)
// Files pair by basename ("paper1.json" + "paper1.pdf"); a dataset export's papers pair by the
// filename it recorded; anything left over can be assigned a PDF by hand.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";

const $ = (s) => document.querySelector(s);
const JSONS = {};    // key -> { name, canonical, schema_id, kind } | { name, error }
const PDFS = {};     // basename -> File
const MANUAL = {};   // json key -> pdf basename chosen by hand
const KEYS = {};     // schema id -> entries key (from /api/schemas), for workspace exports
let EXISTING = null; // documents already in the chosen dataset: [{document_id, filename, title, doi}] (null = new dataset)
const ACTION = {};   // json key -> "replace" | "skip" for papers already in the dataset (default replace)
let PRESETS = [];
let RUNNING = false;

// strip .gemini.json / .json / .pdf so "aejapp_4_3_4.gemini.json" and "aejapp_4_3_4.pdf" pair
const base = (name) => String(name || "").replace(/\.gemini\.json$/i, "").replace(/\.json$/i, "").replace(/\.pdf$/i, "");

function setupDrop() {
  const dz = $("#dz"), input = $("#files");
  dz.onclick = () => input.click();
  input.onchange = () => { addFiles([...input.files]); input.value = ""; };
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => addFiles([...e.dataTransfer.files]));
  $("#run").onclick = run;
  const legal = $("#legal-ok");
  try { legal.checked = sessionStorage.getItem("metalens_legal_ok") === "1"; } catch { /* */ }
  legal.onchange = () => {
    try { if (legal.checked) sessionStorage.setItem("metalens_legal_ok", "1"); else sessionStorage.removeItem("metalens_legal_ok"); } catch { /* */ }
    renderPairs();
  };
  const sel = $("#dataset");
  sel.onchange = async () => {
    $("#dsname").style.display = sel.value === "__new__" ? "" : "none";
    EXISTING = null;
    if (sel.value !== "__new__") {
      try { EXISTING = ((await api.datasetOverview(sel.value)).documents || []); } catch { EXISTING = []; }
    }
    renderPairs();
  };
  const sch = $("#schema");
  sch.onchange = () => { $("#schemaCustom").hidden = sch.value !== "__custom__"; };
  loadDatasets();
  loadPresets();
}

// Populate the "Load into" dropdown with the user's datasets ("New dataset" stays first).
async function loadDatasets() {
  let mine = [];
  try {
    const me = await api.me();
    mine = ((await api.myDatasets()).datasets || []).filter((d) => me && d.owner_user_id === me.id);
  } catch { /* not signed in / none — just the New option */ }
  const sel = $("#dataset");
  for (const d of mine) {
    const o = document.createElement("option");
    o.value = d.id; o.textContent = d.title || "untitled";
    sel.appendChild(o);
  }
}

// "Review as": every preset in the picker (built-in + personal), by its current schema id,
// plus the free "other id" for documents that belong to an older schema row.
async function loadPresets() {
  // "import" keeps the schemas the extract picker hides — the MASEMiner factor-loadings
  // variant and the import-only presets — because those are exactly what lands here
  try { PRESETS = (await api.presets("import")).presets || []; } catch { PRESETS = []; }
  const sel = $("#schema");
  const custom = sel.querySelector("option[value='__custom__']");
  // "import" presets are schemas for data made elsewhere — the very thing this page loads —
  // so they belong here even though the extract picker leaves them out
  for (const p of PRESETS.filter((x) => (x.mode === "extraction" || x.mode === "import") && !x.setup && x.schema_id)) {
    const o = document.createElement("option");
    o.value = p.schema_id; o.textContent = p.title + (p.personal ? " (personal)" : "");
    sel.insertBefore(o, custom);
  }
  const first = PRESETS.find((p) => p.preset_id === "masem-direct") || PRESETS.find((p) => p.schema_id && !p.setup);
  if (first && !sel.dataset.auto) sel.value = first.schema_id;
  sel.onchange();
}

// A file that carries its schema id (an export) picks the matching preset, or fills in the
// custom id when it is an older row the presets no longer mint.
function autoSchema(schemaId) {
  if (!schemaId) return;
  const sel = $("#schema");
  if ([...sel.options].some((o) => o.value === schemaId)) sel.value = schemaId;
  else { sel.value = "__custom__"; $("#schemaCustom").value = schemaId; }
  sel.dataset.auto = "1";
  sel.onchange();
  $("#schemaHint").textContent = `picked up from the file: ${schemaId}`;
}

function currentSchemaId() {
  const sel = $("#schema");
  return sel.value === "__custom__" ? $("#schemaCustom").value.trim() : sel.value;
}

// ── file shapes → one canonical result per paper ────────────────────────────────────────
function classify(obj) {
  if (obj && obj.results && Array.isArray(obj.results.papers)) return "dataset";
  if (obj && Array.isArray(obj.papers) && obj.papers.some((p) => p && Array.isArray(p.records))) return "datasetfile";
  if (obj && typeof obj.extraction === "object" && obj.extraction && !Array.isArray(obj.extraction)) return "wrapped";
  if (obj && Array.isArray(obj.records) && (obj.provenance || (obj.schema_id && obj.paper))) return "workspace";
  return "single";
}

async function entriesKey(schemaId) {
  if (!schemaId) return null;
  if (!(schemaId in KEYS)) {
    try { const s = await api.schema(schemaId); KEYS[schemaId] = (s && s.spec && s.spec.entries && s.spec.entries.key) || null; }
    catch { KEYS[schemaId] = null; }
  }
  return KEYS[schemaId];
}

// A workspace export lists records flat under "records" with the confidence beside them;
// the pipeline wants the preset's own entries key and the ratings back on each entry.
async function fromWorkspaceExport(obj) {
  const key = (await entriesKey(obj.schema_id)) || "records";
  const entries = (obj.records || []).map((r) => JSON.parse(JSON.stringify(r || {})));
  for (const c of obj.confidence || []) {
    const rec = entries[c.entry_index]; if (!rec) continue;
    const groups = {};
    for (const [g, v] of Object.entries(c)) if (g !== "entry_index" && g !== "children" && v && typeof v === "object") groups[g] = v;
    if (Object.keys(groups).length) rec.confidence = groups;
    for (const [rowPath, cg] of Object.entries(c.children || {})) {
      const m = rowPath.match(/^([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$/);
      const row = m && Array.isArray(rec[m[1]]) ? rec[m[1]][+m[2]] : null;
      if (row && typeof row === "object") row.confidence = cg;
    }
  }
  return { paper_metadata: obj.paper_metadata || {}, [key]: entries, evidence: obj.evidence || [] };
}

// The citable dataset file groups records under each paper as {entry_index, values}; a paper
// may also carry an "evidence" list (hand-added citations, one item per quote, "field" a path
// or a list of paths). Screened papers (no records) have nothing to review and are skipped.
async function fromDatasetFile(obj) {
  const sid = (obj.recipe && obj.recipe.schema_id) || null;
  const key = (await entriesKey(sid)) || "records";
  const out = [];
  for (const paper of obj.papers || []) {
    const recs = (paper.records || []).filter((r) => r && r.values && typeof r.values === "object");
    if (!recs.length) continue;
    recs.sort((a, b) => (a.entry_index ?? 0) - (b.entry_index ?? 0));
    out.push({ filename: paper.filename, result: normalizeResult({
      paper_metadata: paper.paper || {}, [key]: recs.map((r) => r.values), evidence: paper.evidence || [] }) });
  }
  return { schema_id: sid, papers: out, title: obj.title || null };
}

async function addFiles(files) {
  for (const f of files) {
    if (/\.json$/i.test(f.name)) {
      let obj;
      try { obj = JSON.parse(await f.text()); }
      catch { JSONS[base(f.name)] = { name: f.name, error: "invalid JSON" }; continue; }
      const kind = classify(obj);
      if (kind === "dataset" || kind === "datasetfile") {
        const ds = kind === "dataset" ? { schema_id: (obj.metadata && obj.metadata.schema_id) || null, papers: obj.results.papers || [] }
                                      : await fromDatasetFile(obj);
        ds.papers.forEach((paper, i) => {
          const key = base(paper.filename || `${base(f.name)}-paper-${i + 1}`);
          JSONS[key] = { name: `${f.name} › ${paper.filename || "paper " + (i + 1)}`, canonical: paper.result, schema_id: ds.schema_id, kind: "dataset" };
        });
        const name = $("#dsname");                                            // suggest the dataset's own name
        if (ds.title && (!name.value || name.value === name.defaultValue)) name.value = ds.title;
        autoSchema(ds.schema_id);
      } else {
        const sid = obj.schema_id || (obj.extraction && obj.extraction.schema_id) || null;
        const canonical = normalizeResult(kind === "wrapped" ? obj.extraction : kind === "workspace" ? await fromWorkspaceExport(obj) : obj);
        // a result that names its own PDF ("source_pdf") pairs with it even when the file names differ
        const hint = canonical && typeof canonical.source_pdf === "string" ? base(canonical.source_pdf.split(/[\\/]/).pop()) : null;
        JSONS[base(f.name)] = { name: f.name, canonical, schema_id: sid, kind, pdfHint: hint };
        autoSchema(sid);
      }
    } else if (/\.pdf$/i.test(f.name)) {
      PDFS[base(f.name)] = f;
    }
  }
  renderPairs();
}

// Results from other pipelines often call the paper block "paper" (with a "venue") instead
// of "paper_metadata" (with a "journal"); map it so title / authors / year land in the viewer.
function normalizeResult(obj) {
  if (!obj || typeof obj !== "object" || obj.paper_metadata || !obj.paper || typeof obj.paper !== "object") return obj;
  const { venue, ...pm } = obj.paper;
  if (venue != null && pm.journal == null) pm.journal = venue;
  const { paper, ...rest } = obj;
  return { paper_metadata: pm, ...rest };
}

// ── already in the dataset? ─────────────────────────────────────────────────────────────
// Same rule as the server's duplicate check: DOI, else title, else filename. Re-importing a
// corrected file into the dataset it came from is the normal case — the old copy should go.
const paperKeys = (doi, title, filename) => {
  const out = [];
  const d = String(doi || "").trim().toLowerCase().replace(/^https?:\/\/(dx\.)?doi\.org\//, "");
  if (d) out.push("doi:" + d);
  const t = String(title || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  if (t) out.push("title:" + t);
  const f = base(String(filename || "")).toLowerCase();
  if (f) out.push("file:" + f);
  return out;
};
function existingFor(key) {
  if (!EXISTING || !EXISTING.length) return [];
  const j = JSONS[key]; if (!j || j.error) return [];
  const pm = (j.canonical && j.canonical.paper_metadata) || {};
  const mine = new Set(paperKeys(pm.doi, pm.title, key));
  return EXISTING.filter((d) => paperKeys(d.doi, d.title, d.filename).some((k) => mine.has(k)));
}

// ── pairing ─────────────────────────────────────────────────────────────────────────────
// a JSON pairs with the PDF of the same basename, the PDF it names in "source_pdf", or the
// one assigned by hand
const pdfKeyFor = (key) => {
  if (MANUAL[key]) return MANUAL[key];
  if (PDFS[key]) return key;
  const hint = JSONS[key] && JSONS[key].pdfHint;
  return hint && PDFS[hint] ? hint : null;
};
const pdfFor = (key) => { const k = pdfKeyFor(key); return k ? PDFS[k] : null; };
const claimed = () => new Set(Object.keys(JSONS).map(pdfKeyFor).filter(Boolean));

function allRows() {
  const taken = claimed();
  const rows = Object.keys(JSONS).sort().map((k) => ({ key: k, json: JSONS[k], pdf: pdfFor(k) }));
  for (const k of Object.keys(PDFS).sort()) if (!taken.has(k)) rows.push({ key: k, json: null, pdf: PDFS[k], orphanPdf: true });
  return rows;
}
const importable = (r) => r.json && !r.json.error;

function renderPairs() {
  const rows = allRows();
  const taken = claimed();
  const spare = Object.keys(PDFS).filter((k) => !taken.has(k));
  $("#pairs").innerHTML = rows.length ? rows.map((r) => {
    let state, cls;
    if (r.json && r.json.error) { state = r.json.error; cls = "warn"; }
    else if (!r.json) { state = "a PDF without its .json — assign it to a paper below, or add the JSON"; cls = "warn"; }
    else if (r.pdf) { state = `ready · ${r.json.kind === "dataset" ? "from dataset export" : r.json.kind === "workspace" ? "workspace export" : "result"}`; cls = "ok"; }
    else {
      state = `no PDF — imports without page images` + (spare.length
        ? ` · <select class="assign-pdf" data-k="${esc(r.key)}"><option value="">assign a PDF…</option>`
          + spare.map((k) => `<option value="${esc(k)}">${esc(k)}.pdf</option>`).join("") + `</select>` : "");
      cls = "ok";
    }
    const dup = r.json && !r.json.error ? existingFor(r.key) : [];
    if (dup.length) {
      const act = ACTION[r.key] || "replace";
      if (!r.pdf) state = state.replace("no PDF — imports without page images", "no PDF dropped — the old copy's PDF is kept");
      state += ` · <span class="ir-dup">already in this dataset (${dup.length})</span> <select class="dup-action" data-k="${esc(r.key)}">`
        + `<option value="replace"${act === "replace" ? " selected" : ""}>replace the old copy</option>`
        + `<option value="skip"${act === "skip" ? " selected" : ""}>skip this paper</option></select>`;
      cls = act === "skip" ? "warn" : cls;
    }
    return `<div class="import-row ${cls}" data-k="${esc(r.key)}">`
      + `<span class="ir-name">${esc(r.json ? r.json.name : r.key + ".pdf")}</span><span class="ir-stat">${state}</span></div>`;
  }).join("") : '<p class="muted" style="padding:8px 0">Drop <code>.json</code> results (single papers, a workspace export, or a dataset export) and their <code>.pdf</code> files.</p>';
  $("#pairs").querySelectorAll(".dup-action").forEach((sel) => (sel.onchange = () => { ACTION[sel.dataset.k] = sel.value; renderPairs(); }));
  $("#pairs").querySelectorAll(".assign-pdf").forEach((sel) => (sel.onchange = () => {
    if (sel.value) MANUAL[sel.dataset.k] = sel.value; else delete MANUAL[sel.dataset.k];
    renderPairs();
  }));
  const n = rows.filter(importable).length;
  const run = $("#run");
  const legalOk = $("#legal-ok").checked;
  run.disabled = !n || RUNNING || !legalOk;
  run.title = n && !legalOk ? "confirm lawful access to the files first" : "";
  run.textContent = n ? `Import ${n} paper${n === 1 ? "" : "s"}` : "Import";
}

function setStat(key, html, cls) {
  const row = document.querySelector(`.import-row[data-k="${CSS.escape(key)}"]`);
  if (row) { row.classList.remove("ok", "warn"); if (cls) row.classList.add(cls); row.querySelector(".ir-stat").innerHTML = html; }
}

async function run() {
  if (RUNNING) return;
  if (!$("#legal-ok").checked) { $("#status").textContent = "please confirm lawful access to the files first"; return; }
  RUNNING = true; $("#run").disabled = true;
  const schemaId = currentSchemaId();
  const rows = allRows().filter(importable);

  // Resolve the target dataset: a new one (default → these uploads shown in isolation) or an
  // existing one to merge into.
  let datasetId = $("#dataset").value;
  try {
    if (datasetId === "__new__") {
      const title = ($("#dsname").value || "").trim() || "Imported extractions";
      const ds = await api.createDataset({ title, visibility: "private", schema_id: schemaId || null });
      datasetId = ds.id;
    }
  } catch (e) {
    RUNNING = false; $("#run").disabled = false;
    $("#status").textContent = "Couldn't create the dataset: " + e.message;
    return;
  }

  let done = 0, replaced = 0, skipped = 0;
  for (const r of rows) {
    const dup = existingFor(r.key);
    if (dup.length && (ACTION[r.key] || "replace") === "skip") { skipped++; setStat(r.key, "skipped — already in the dataset", "warn"); continue; }
    setStat(r.key, '<span class="spin"></span> importing…');
    try {
      // Replacing: import OUTSIDE the dataset first (the server refuses a second copy of the
      // same PDF inside one dataset), delete the old copies, then attach the new document —
      // so the paper is never missing from the dataset, and never in it twice.
      const target = dup.length ? null : datasetId;
      let res;
      if (r.pdf) {
        const fd = new FormData();
        fd.append("pdf", r.pdf);
        fd.append("result", JSON.stringify(r.json.canonical));
        if (schemaId) fd.append("schema_id", schemaId);
        if (target) fd.append("dataset_id", target);
        res = await api.ingestPdf(fd);
      } else {
        // no PDF dropped: a replaced copy that HAS one lends it, so page images and
        // highlights survive the re-import instead of being deleted with the old copy
        res = await api.ingest({ result: r.json.canonical, schema_id: schemaId || null, dataset_id: target || null,
                                 pdf_from_document_id: dup.length ? dup[0].document_id : null });
      }
      done++;
      let gone = 0;
      if (dup.length) {
        for (const d of dup) {
          if (d.document_id === res.document_id) continue;
          try { await api.deleteDocument(d.document_id); gone++; } catch { /* leave it; the dataset page can dedupe */ }
        }
        if (datasetId) await api.addToDataset(datasetId, { document_id: res.document_id });
      }
      if (gone) replaced++;
      const pages = r.pdf || (res.n_pages > 0);
      setStat(r.key, `✓ ${res.n_records} record${res.n_records === 1 ? "" : "s"}${pages ? (r.pdf ? "" : " · PDF kept from the old copy") : " · no page images"}${gone ? ` · replaced ${gone} old cop${gone === 1 ? "y" : "ies"}` : ""}`, "ok");
    } catch (e) { setStat(r.key, `✗ ${esc(e.message)}`, "warn"); }
  }
  RUNNING = false; $("#run").disabled = false;
  const go = datasetId ? `/workspace?project=${encodeURIComponent(datasetId)}` : "/workspace";
  const extra = (replaced ? `, ${replaced} replaced` : "") + (skipped ? `, ${skipped} skipped` : "");
  $("#status").innerHTML = `Imported ${done}/${rows.length}${extra}. <a href="${go}">Open Data review →</a>`;
  if (done) setTimeout(() => { location.href = go; }, 800);
}

setupDrop();
