// /presets — the preset inventory: one card per curated preset (a preset with a card), with its
// latest release and DOI, its authors, and counts of what was built with it. The tiles reuse the
// Dashboards page's markup; the server already filtered by brand.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { iconSvg } from "/static/dash/icons.js";
const $ = (s) => document.querySelector(s);
const n = (k, w) => `${k} ${w}${k === 1 ? "" : "s"}`;
const name = (a) => [a.given, a.family].filter(Boolean).join(" ");
const by = (authors) => { const xs = (authors || []).map(name); return xs.length <= 2 ? xs.join(" & ") : xs.slice(0, -1).join(", ") + " & " + xs[xs.length - 1]; };
const kws = (list) => ((list || []).length ? `<div class="tile-kws">${list.slice(0, 7).map((k) => `<span class="kw">${esc(k)}</span>`).join("")}</div>` : "");
const kicker = (p) => p.latest
  ? `Preset · v${p.latest.number}${p.latest.doi ? ` · DOI ${esc(p.latest.doi)}` : ""}`
  : `Preset · unreleased · <code>${esc(p.schema_id || "")}</code>`;
const counts = (c) => [n(c.datasets, "dataset"), n(c.records, "record"), n(c.papers, "paper"), n(c.dashboards, "dashboard")].join(" · ");

function tile(p) {
  return `<article class="tile${p.hidden ? " tile-hidden" : ""}"><a class="tile-a" href="/preset?id=${encodeURIComponent(p.id)}">
    <div class="tile-img"><div class="tile-sketch">${iconSvg("rows_table", {})}</div></div>
    <div class="tile-body"><span class="tile-k">${kicker(p)}${p.hidden ? ` <span class="tile-new">builder-only</span>` : ""}${p.mode === "import" ? ` <span class="tile-new">import schema</span>` : ""}</span>
      <h3 class="tile-h">${esc(p.title || p.id)}</h3>${kws(p.keywords)}
      <p class="tile-p clamp">${esc(p.tagline || p.description || "")}</p>
      ${(p.authors || []).length ? `<p class="tile-by">${esc(by(p.authors))}</p>` : ""}</div></a>
    <div class="tile-foot">${counts(p.counts || { datasets: 0, records: 0, papers: 0, dashboards: 0 })}${p.mode === "import" ? " · data produced outside Metalens" : ""}</div></article>`;
}

async function init() {
  let data;
  try { data = await api.presetCards(); }
  catch (e) { $("#pr-tiles").innerHTML = `<p class="muted">Could not load: ${esc(e.message)}</p>`; return; }
  const cards = data.cards || [];
  $("#pr-tiles").innerHTML = cards.length ? cards.map(tile).join("") : `<p class="muted">No preset is documented yet.</p>`;
}
init();
