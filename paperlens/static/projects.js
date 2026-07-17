// My Workspace — the signed-in hub. Two sections: Datasets (extracted records,
// each opening its overview) and Analyses (saved views/dashboards over ≥1 dataset).
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";

async function init() {
  const dsGrid = document.querySelector("#datasets");
  const anGrid = document.querySelector("#analyses");
  const preGrid = document.querySelector("#presets");
  const sub = document.querySelector("#psub");
  const me = await api.me();
  if (!me || !me.email) {
    sub.textContent = "Sign in to see your workspace.";
    dsGrid.innerHTML = '<p class="muted">Use “Sign in” in the top-right, then your saved '
      + 'datasets, presets and analyses appear here. Save a dataset from the extraction results.</p>';
    anGrid.innerHTML = ""; if (preGrid) preGrid.innerHTML = "";
    return;
  }
  renderDatasets(dsGrid, me);
  renderPresets(preGrid, me);
  renderAnalyses(anGrid, me);
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
    if (!confirm("Delete this dataset? Its records become private again (the underlying documents are kept).")) return;
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

init();
