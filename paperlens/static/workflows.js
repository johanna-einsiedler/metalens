// /workflows — the workflow inventory: one card per curated workflow (a workflow with a card),
// with its steps, its review policy, its latest release and DOI, and counts of what was built
// with it. The tiles reuse the Presets page's markup; the server already filtered by brand.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { iconSvg } from "/static/dash/icons.js";
const $ = (s) => document.querySelector(s);
const n = (k, w) => `${k} ${w}${k === 1 ? "" : "s"}`;
const name = (a) => [a.given, a.family].filter(Boolean).join(" ");
const by = (authors) => { const xs = (authors || []).map(name); return xs.length <= 2 ? xs.join(" & ") : xs.slice(0, -1).join(", ") + " & " + xs[xs.length - 1]; };
const kws = (list) => ((list || []).length ? `<div class="tile-kws">${list.slice(0, 4).map((k) => `<span class="kw">${esc(k)}</span>`).join("")}</div>` : "");
const kicker = (w) => w.latest
  ? `Workflow · v${w.latest.number}${w.latest.doi ? ` · DOI ${esc(w.latest.doi)}` : ""}`
  : `Workflow · unreleased · <code>${esc(w.contract || "")}</code>`;
const review = (w) => (w.review || []).map((r) => (r.unit === "row" ? `${r.mode === "sample" ? `${r.per_paper || 1} of ${r.table}` : `every ${r.table}`} per paper` : r.mode === "sample" ? `sample of ${r.n != null ? r.n : Math.round((r.share || 0) * 100) + "%"}` : `every ${r.unit === "paper" ? "paper" : "record"}`) + (r.coders ? ` by ${r.coders}` : "")).join(" · ");
const steps = (w) => (w.steps || []).map((s) => `<span class="kw" title="${esc(s.kind)}">${esc(s.label || s.id)}</span>`).join(" → ");
const counts = (c) => [n(c.datasets, "dataset"), n(c.papers, "paper"), n(c.records, "record"), n(c.runs, "run")].join(" · ");

function tile(w) {
  return `<article class="tile${w.hidden ? " tile-hidden" : ""}"><a class="tile-a" href="/workflow?id=${encodeURIComponent(w.id)}">
    <div class="tile-img"><div class="tile-sketch">${iconSvg("rows_table", {})}</div></div>
    <div class="tile-body"><span class="tile-k">${kicker(w)}</span>
      <h3 class="tile-h">${esc(w.title || w.id)}</h3>${kws(w.keywords)}
      <p class="tile-p clamp">${esc(w.tagline || w.description || "")}</p>
      <p class="tile-by muted" style="font-size:12px">${steps(w)}${review(w) ? ` · review: ${esc(review(w))}` : ""}</p>
      ${(w.authors || []).length ? `<p class="tile-by">${esc(by(w.authors))}</p>` : ""}</div></a>
    <div class="tile-foot">${counts(w.counts || { datasets: 0, papers: 0, records: 0, runs: 0 })}</div></article>`;
}

// the user's own workflows (and the ones others shared): plain tiles, an Edit link on the owned ones
async function renderMine() {
  const sec = $("#wf-mine-section"), grid = $("#wf-mine"); if (!sec || !grid) return;
  let list = [];
  try { list = ((await api.workflows()).workflows || []).filter((w) => w.personal); } catch { return; }
  if (!list.length) return;
  sec.hidden = false;
  grid.innerHTML = list.map((w) => `<div class="proj-tile-wrap"><a class="proj-tile" href="/workflow?id=${encodeURIComponent(w.id)}">
      <div class="pt-title">${esc(w.title || w.id)}</div>
      <div class="pt-meta">${steps(w)}</div>
      <div class="pt-meta"><code>${esc(w.contract || "")}</code> · v${esc(String(w.version || 1))} · <span class="pt-vis ${esc(w.visibility || "private")}">${esc(w.visibility || "private")}</span>${w.owned ? "" : " · shared with you"}</div></a>
      ${w.owned ? `<a class="pt-del" href="/workflow-editor?id=${encodeURIComponent(w.id)}" title="edit">✎</a>` : ""}</div>`).join("");
}

async function init() {
  renderMine();
  let data;
  try { data = await api.workflowCards(); }
  catch (e) { $("#wf-tiles").innerHTML = `<p class="muted">Could not load: ${esc(e.message)}</p>`; return; }
  const cards = data.cards || [];
  $("#wf-tiles").innerHTML = cards.length ? cards.map(tile).join("") : `<p class="muted">No workflow is documented yet.</p>`;
}
init();
