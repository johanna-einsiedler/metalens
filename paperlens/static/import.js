// Import pre-computed extractions (JSON) + their source PDFs → full viewable documents with
// highlighting, via POST /api/ingest-pdf (which reuses the extraction pipeline minus the
// model call). Files are paired by basename; a ".gemini.json" suffix + an "extraction"
// wrapper are normalised away.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";

const $ = (s) => document.querySelector(s);
const JSONS = {};   // basename -> { file, canonical } | { file, error }
const PDFS = {};    // basename -> File
let RUNNING = false;

// strip .gemini.json / .json / .pdf so "aejapp_4_3_4.gemini.json" and "aejapp_4_3_4.pdf" pair
const base = (name) => name.replace(/\.gemini\.json$/i, "").replace(/\.json$/i, "").replace(/\.pdf$/i, "");

function setupDrop() {
  const dz = $("#dz"), input = $("#files");
  dz.onclick = () => input.click();
  input.onchange = () => { addFiles([...input.files]); input.value = ""; };
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("drag"); });
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", (e) => addFiles([...e.dataTransfer.files]));
  $("#run").onclick = run;
  const sel = $("#dataset");
  sel.onchange = () => { $("#dsname").style.display = sel.value === "__new__" ? "" : "none"; };
  loadDatasets();
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

async function addFiles(files) {
  for (const f of files) {
    const b = base(f.name);
    if (/\.json$/i.test(f.name)) {
      try {
        const obj = JSON.parse(await f.text());
        const canonical = (obj && typeof obj.extraction === "object" && obj.extraction) ? obj.extraction : obj;
        JSONS[b] = { file: f, canonical };
      } catch { JSONS[b] = { file: f, error: "invalid JSON" }; }
    } else if (/\.pdf$/i.test(f.name)) {
      PDFS[b] = f;
    }
  }
  renderPairs();
}

function allPairs() {
  return [...new Set([...Object.keys(JSONS), ...Object.keys(PDFS)])].sort()
    .map((k) => ({ key: k, json: JSONS[k], pdf: PDFS[k] }));
}
const ready = (p) => p.json && !p.json.error && p.pdf;

function renderPairs() {
  const ps = allPairs();
  $("#pairs").innerHTML = ps.length ? ps.map((p) => {
    const state = p.json && p.json.error ? p.json.error
      : !p.json ? "waiting for its .json" : !p.pdf ? "waiting for its .pdf" : "ready";
    return `<div class="import-row ${ready(p) ? "ok" : "warn"}" data-k="${esc(p.key)}">`
      + `<span class="ir-name">${esc(p.key)}</span><span class="ir-stat">${esc(state)}</span></div>`;
  }).join("") : '<p class="muted" style="padding:8px 0">Drop matching <code>.json</code> + <code>.pdf</code> files (same base name).</p>';
  const n = ps.filter(ready).length;
  const run = $("#run");
  run.disabled = !n || RUNNING;
  run.textContent = n ? `Import ${n} paper${n === 1 ? "" : "s"}` : "Import";
}

function setStat(key, html, cls) {
  const row = document.querySelector(`.import-row[data-k="${CSS.escape(key)}"]`);
  if (row) { row.classList.remove("ok", "warn"); if (cls) row.classList.add(cls); row.querySelector(".ir-stat").innerHTML = html; }
}

async function run() {
  if (RUNNING) return;
  RUNNING = true; $("#run").disabled = true;
  const schemaId = $("#schema").value.trim();
  const pairs = allPairs().filter(ready);

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

  let done = 0;
  for (const p of pairs) {
    setStat(p.key, '<span class="spin"></span> importing…');
    try {
      const fd = new FormData();
      fd.append("pdf", p.pdf);
      fd.append("result", JSON.stringify(p.json.canonical));
      if (schemaId) fd.append("schema_id", schemaId);
      if (datasetId) fd.append("dataset_id", datasetId);
      const r = await api.ingestPdf(fd);
      done++;
      setStat(p.key, `✓ ${r.n_records} record${r.n_records === 1 ? "" : "s"}`, "ok");
    } catch (e) { setStat(p.key, `✗ ${esc(e.message)}`, "warn"); }
  }
  RUNNING = false; $("#run").disabled = false; renderPairs();
  const go = datasetId ? `/workspace?project=${encodeURIComponent(datasetId)}` : "/workspace";
  $("#status").innerHTML = `Imported ${done}/${pairs.length}. <a href="${go}">Open Data review →</a>`;
  if (done) setTimeout(() => { location.href = go; }, 800);
}

setupDrop();
