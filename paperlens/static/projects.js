// My Workspace — the signed-in hub. Two sections: Datasets (extracted records,
// each opening its overview) and Analyses (saved views/dashboards over ≥1 dataset).
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";

async function init() {
  const dsGrid = document.querySelector("#datasets");
  const anGrid = document.querySelector("#analyses");
  const preGrid = document.querySelector("#presets");
  const papGrid = document.querySelector("#papers");
  const sub = document.querySelector("#psub");
  const me = await api.me();
  if (!me || !me.email) {
    sub.textContent = "Sign in to see your workspace.";
    dsGrid.innerHTML = '<p class="muted">Use “Sign in” in the top-right, then your saved '
      + 'datasets, presets and analyses appear here. Save a dataset from the extraction results.</p>';
    anGrid.innerHTML = ""; if (preGrid) preGrid.innerHTML = ""; if (papGrid) papGrid.innerHTML = "";
    return;
  }
  renderDatasets(dsGrid, me);
  renderPresets(preGrid, me);
  renderAnalyses(anGrid, me);
  renderPapers(papGrid, me);
}

async function renderPresets(grid, _me) {
  if (!grid) return;
  let presets = [];
  try { presets = (await api.myPresets()).presets || []; }
  catch (e) { grid.innerHTML = `<p class="muted">error: ${esc(e.message)}</p>`; return; }
  if (!presets.length) {
    grid.innerHTML = '<p class="muted">No personal presets yet. Click '
      + '<a href="/preset">＋ New preset</a> to define your own extraction (prompt + review tabs).</p>';
    return;
  }
  grid.innerHTML = presets.map((p) =>
    `<div class="proj-tile-wrap">`
    + `<a class="proj-tile" href="/preset?id=${encodeURIComponent(p.id)}">`
    + `<div class="pt-title">${esc(p.title)}</div>`
    + `<div class="pt-meta">${esc(p.tagline || p.mode || "preset")} · `
    + `<span class="pt-vis ${esc(p.visibility)}">${esc(p.visibility)}</span></div></a>`
    + `<button class="pt-del" data-id="${esc(p.id)}" title="delete preset">🗑</button></div>`).join("");
  grid.querySelectorAll(".pt-del").forEach((b) => (b.onclick = async (e) => {
    e.preventDefault();
    if (!confirm("Delete this preset? Datasets already built with it keep working.")) return;
    try { await api.deletePreset(b.dataset.id); b.closest(".proj-tile-wrap").remove(); }
    catch (ex) { alert("delete failed: " + ex.message); }
  }));
}

async function renderDatasets(grid, me) {
  let datasets = [];
  try { datasets = (await api.myDatasets()).datasets || []; }
  catch (e) { grid.innerHTML = `<p class="muted">error: ${esc(e.message)}</p>`; return; }
  const mine = datasets.filter((d) => d.owner_user_id && d.owner_user_id === me.id);
  if (!mine.length) {
    grid.innerHTML = '<p class="muted">No datasets yet. Extract a paper, then click '
      + '“💾 Save to my workspace” to create one.</p>';
    return;
  }
  grid.innerHTML = mine.map((d) =>
    `<div class="proj-tile-wrap">`
    + `<a class="proj-tile" href="/dataset?id=${esc(d.id)}">`
    + `<div class="pt-title">${esc(d.title)}</div>`
    + `<div class="pt-meta">${d.n_records} record${d.n_records === 1 ? "" : "s"} · `
    + `<span class="pt-vis ${esc(d.visibility)}">${esc(d.visibility)}</span></div></a>`
    + `<button class="pt-del" data-id="${esc(d.id)}" title="delete dataset">🗑</button></div>`).join("");
  grid.querySelectorAll(".pt-del").forEach((b) => (b.onclick = async (e) => {
    e.preventDefault();
    if (!confirm("Delete this dataset? Its extracted records are discarded, but each paper stays in “All my papers” so you can re-extract it into another dataset.")) return;
    try { await api.deleteDataset(b.dataset.id); b.closest(".proj-tile-wrap").remove(); }
    catch (ex) { alert("delete failed: " + ex.message); }
  }));
}

// Analyses/dashboards are disabled in the beta — show a "coming soon" note
// instead of the saved-view grid (the underlying data stays under Data review).
async function renderAnalyses(grid, _me) {
  grid.innerHTML = '<p class="muted">🚧 Dashboards &amp; saved analyses are coming soon. '
    + 'For now, extract papers and review the data under <a href="/workspace">Data review</a>.</p>';
}

// "All my papers" — the cached-PDF library (one card per distinct PDF, deduped by
// content hash server-side). A paper persists here after its dataset is deleted, and
// can be (re-)extracted into any dataset with that dataset's recipe.
async function renderPapers(grid, _me) {
  if (!grid) return;
  let papers = [];
  try { papers = (await api.myPapers()).papers || []; }
  catch (e) { grid.innerHTML = `<p class="muted">error: ${esc(e.message)}</p>`; return; }
  if (!papers.length) {
    grid.innerHTML = '<p class="muted">No papers yet. <a href="/extract">Process a paper</a> and it appears here.</p>';
    return;
  }
  grid.innerHTML = papers.map((p) => {
    const name = p.title || p.filename || "untitled";
    // n_records 0 = cached PDF with no current extraction (e.g. its dataset was deleted)
    const bits = [p.n_records ? `${p.n_records} record${p.n_records === 1 ? "" : "s"}` : "not extracted"];
    if (p.n_pages) bits.push(`${p.n_pages} pp`);
    if (p.n_extractions > 1) bits.push(`${p.n_extractions} extractions`);
    const ds = p.datasets || [];
    const inDs = ds.length
      ? "in " + ds.slice(0, 2).map((d) => esc(d.title || "untitled")).join(", ") + (ds.length > 2 ? ` +${ds.length - 2}` : "")
      : "not in a dataset";
    return `<div class="proj-tile-wrap">`
      + `<div class="proj-tile">`
      + `<a class="pt-title" href="/workspace?doc=${esc(p.document_id)}" title="Open in Data review">${esc(name)}</a>`
      + `<div class="pt-meta">${esc(bits.join(" · "))}</div>`
      + `<div class="pt-meta pt-inds">${inDs}</div>`
      + `<div class="pt-actions"><button class="btn btn-ghost btn-sm pt-add" `
      + `data-sha="${esc(p.pdf_sha256)}" data-doc="${esc(p.document_id)}" `
      + `data-name="${esc(p.filename || (name + ".pdf"))}">＋ Add to dataset</button></div>`
      + `</div>`
      + `<button class="pt-del" data-sha="${esc(p.pdf_sha256)}" title="remove paper (deletes all its extractions)">🗑</button></div>`;
  }).join("");
  grid.querySelectorAll(".pt-del").forEach((b) => (b.onclick = async (e) => {
    e.preventDefault();
    if (!confirm("Remove this paper from your library? This permanently deletes all of its extractions and its stored PDF. This cannot be undone.")) return;
    try { await api.deletePaper(b.dataset.sha); b.closest(".proj-tile-wrap").remove(); }
    catch (ex) { alert("delete failed: " + ex.message); }
  }));
  grid.querySelectorAll(".pt-add").forEach((b) => (b.onclick = async () => {
    const dsId = await chooseDataset();
    if (dsId) location.href = `/extract?dataset=${encodeURIComponent(dsId)}`
      + `&source=${encodeURIComponent(b.dataset.doc)}&name=${encodeURIComponent(b.dataset.name)}`;
  }));
}

// Small chooser: pick one of the user's own datasets to add a paper to. Resolves the
// dataset id (or null if cancelled). The /extract page then re-extracts with its recipe.
async function chooseDataset() {
  let datasets = [];
  try { datasets = (await api.myDatasets()).datasets || []; }
  catch (e) { alert("could not load datasets: " + e.message); return null; }
  const mine = datasets.filter((d) => d.owner_user_id);
  return new Promise((resolve) => {
    const ov = document.createElement("div");
    ov.className = "modal-overlay";
    const opts = mine.map((d) =>
      `<button class="btn btn-ghost ds-pick" data-id="${esc(d.id)}">${esc(d.title || "untitled")}</button>`).join("");
    ov.innerHTML = `<div class="modal" role="dialog" aria-modal="true">`
      + `<h3>Add to dataset</h3>`
      + `<p class="muted">Re-extract this paper with the chosen dataset's prompt &amp; model, then add it.</p>`
      + `<div class="ds-pick-list">${opts || '<p class="muted">You have no datasets yet — create one from an extraction first.</p>'}</div>`
      + `<div class="modal-actions"><button class="btn btn-ghost pick-cancel">Cancel</button></div></div>`;
    document.body.appendChild(ov);
    const close = (val) => { ov.remove(); resolve(val); };
    ov.querySelector(".pick-cancel").onclick = () => close(null);
    ov.addEventListener("click", (e) => { if (e.target === ov) close(null); });
    ov.querySelectorAll(".ds-pick").forEach((b) => (b.onclick = () => close(b.dataset.id)));
  });
}

init();
