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
  let done = 0;
  for (const p of pairs) {
    setStat(p.key, '<span class="spin"></span> importing…');
    try {
      const fd = new FormData();
      fd.append("pdf", p.pdf);
      fd.append("result", JSON.stringify(p.json.canonical));
      if (schemaId) fd.append("schema_id", schemaId);
      const r = await api.ingestPdf(fd);
      done++;
      setStat(p.key, `✓ ${r.n_records} record${r.n_records === 1 ? "" : "s"} · `
        + `<a href="/workspace?doc=${esc(r.document_id)}">open →</a>`, "ok");
    } catch (e) { setStat(p.key, `✗ ${esc(e.message)}`, "warn"); }
  }
  RUNNING = false;
  $("#run").disabled = false; renderPairs();
  $("#status").innerHTML = `Imported ${done}/${pairs.length}. <a href="/workspace">Open Data review →</a>`;
}

setupDrop();
