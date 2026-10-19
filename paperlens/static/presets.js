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
const kws = (list) => ((list || []).length ? `<div class="tile-kws">${list.slice(0, 4).map((k) => `<span class="kw">${esc(k)}</span>`).join("")}</div>` : "");
// the preset's preview picture when one ships (static/tiles/presets/<id>.png), else a placeholder sketch
const picture = (p) => (p.tile_url ? `<div class="tile-img"><img src="${esc(p.tile_url)}" alt="" loading="lazy"></div>`
  : `<div class="tile-img"><div class="tile-sketch">${iconSvg("rows_table", {})}</div></div>`);
const kicker = (p) => p.latest
  ? `Preset · v${p.latest.number}${p.latest.doi ? ` · DOI ${esc(p.latest.doi)}` : ""}`
  : `Preset · unreleased · <code>${esc(p.schema_id || "")}</code>`;
const counts = (c) => [n(c.datasets, "dataset"), n(c.records, "record"), n(c.papers, "paper"), n(c.dashboards, "dashboard")].join(" · ");

function tile(p) {
  return `<article class="tile${p.hidden ? " tile-hidden" : ""}"><a class="tile-a" href="/preset?id=${encodeURIComponent(p.id)}">
    ${picture(p)}
    <div class="tile-body"><span class="tile-k">${kicker(p)}${p.hidden ? ` <span class="tile-new">builder-only</span>` : ""}${p.mode === "import" ? ` <span class="tile-new">import schema</span>` : ""}</span>
      <h3 class="tile-h">${esc(p.title || p.id)}</h3>${kws(p.keywords)}
      <p class="tile-p clamp">${esc(p.tagline || p.description || "")}</p>
      ${(p.authors || []).length ? `<p class="tile-by">${esc(by(p.authors))}</p>` : ""}</div></a>
    <div class="tile-foot">${counts(p.counts || { datasets: 0, records: 0, papers: 0, dashboards: 0 })}${p.mode === "import" ? " · data produced outside Metalens" : ""}</div></article>`;
}

// ── a preset someone sent as a file: check it, then save it as the viewer's own preset ──────────
const errList = (xs) => `<ul class="pr-errs">${xs.map((x) => `<li>${esc(typeof x === "string" ? x : JSON.stringify(x))}</li>`).join("")}</ul>`;
async function loadFile(file) {
  const box = $("#pr-loaded"); box.hidden = false;
  const show = (html) => { box.innerHTML = `<button class="pr-x" type="button" aria-label="close">✕</button>${html}`; box.querySelector(".pr-x").onclick = () => { box.hidden = true; }; };
  let spec;
  try { spec = JSON.parse(await file.text()); }
  catch (e) { show(`<p><b>${esc(file.name)}</b> is not a JSON file: ${esc(e.message)}</p>`); return; }
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) { show(`<p><b>${esc(file.name)}</b> does not hold a preset (a JSON object with <code>meta</code>, <code>entries</code>, …).</p>`); return; }
  let v;
  try { v = await api.validatePreset(spec); } catch (e) { show(`<p>Could not check <b>${esc(file.name)}</b>: ${esc(e.message)}</p>`); return; }
  const meta = spec.meta || {};
  const head = `<div class="ds-card-h">${esc(meta.title || file.name)}</div>${meta.tagline ? `<p class="muted pr-tag">${esc(meta.tagline)}</p>` : ""}`;
  if (!v.ok) {
    show(`${head}<p>This preset cannot be saved yet: fix ${v.errors.length === 1 ? "this problem" : `these ${v.errors.length} problems`} in the file and load it again.</p>${errList(v.errors)}`);
    return;
  }
  const me = await api.me();
  show(`${head}
    <p class="pr-meta">Schema <code>${esc(v.schema_id || "")}</code>${meta.mode === "import" ? " · import schema (for results made elsewhere)" : ""}</p>
    ${(v.warnings || []).length ? `<details class="pr-warn"><summary>${v.warnings.length} warning${v.warnings.length === 1 ? "" : "s"}</summary>${errList(v.warnings)}</details>` : ""}
    <fieldset class="pr-vis"><legend>Who can use it</legend>
      <label><input type="radio" name="pr-vis" value="private" checked> Only me</label>
      <label><input type="radio" name="pr-vis" value="public"${me && me.id ? "" : " disabled"}> Everyone on this site (listed in the extract and import pickers)${me && me.id ? "" : " · sign in first"}</label></fieldset>
    ${me && me.id ? "" : `<p class="muted pr-note">You are not signed in: the preset is kept with this browser session and deleted with it. <a href="/account?next=/presets">Sign in</a> to keep it or to share it with everyone.</p>`}
    <div class="pr-btns"><button class="btn btn-primary" id="pr-save" type="button">Save as my preset</button> <span class="muted" id="pr-status"></span></div>`);
  $("#pr-save").onclick = async () => {
    const vis = (box.querySelector('input[name="pr-vis"]:checked') || {}).value || "private";
    $("#pr-save").disabled = true; $("#pr-status").textContent = "saving…";
    let r;
    try { r = await api.createPreset({ spec, visibility: vis, keep_id: true }); }
    catch (e) { $("#pr-save").disabled = false; $("#pr-status").textContent = `Could not save: ${e.message}`; return; }
    const id = r.id || r.preset_id;
    const moved = r.id_wanted && !r.id_kept
      ? `<p class="pr-note">The id <code>${esc(r.id_wanted)}</code> is already in use here, so it was saved as <code>${esc(id)}</code> and runs under the schema <code>${esc(r.schema_id || "")}</code>.</p>` : "";
    const use = r.mode === "import"
      ? `<a class="btn btn-primary" href="/import">Import papers with it</a>`
      : `<a class="btn btn-primary" href="/extract?preset=${encodeURIComponent(id)}">Run it on papers</a> <a class="btn btn-ghost" href="/import">Import results</a>`;
    show(`${head}<p>Saved as your preset <code>${esc(id)}</code>${vis === "public" ? ", usable by everyone on this site" : ""}. It appears under <i>Review as</i> on the Import page and in the extract picker.</p>${moved}
      <div class="pr-btns">${use} <a class="btn btn-ghost" href="/preset?id=${encodeURIComponent(id)}&edit=1">Open in the editor</a></div>`);
  };
}

async function init() {
  $("#pr-load").onclick = () => $("#pr-file").click();
  $("#pr-file").onchange = (ev) => { const f = ev.target.files && ev.target.files[0]; ev.target.value = ""; if (f) loadFile(f); };
  let data;
  try { data = await api.presetCards(); }
  catch (e) { $("#pr-tiles").innerHTML = `<p class="muted">Could not load: ${esc(e.message)}</p>`; return; }
  const cards = data.cards || [];
  $("#pr-tiles").innerHTML = cards.length ? cards.map(tile).join("") : `<p class="muted">No preset is documented yet.</p>`;
}
init();
