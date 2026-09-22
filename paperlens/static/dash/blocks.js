// A dashboard is a list of blocks; a block is a card around one template. The card states the
// question the block answers and the message it is meant to carry, then the template draws,
// then a caption says how much data stands behind it (rows, studies, how many unverified).
import { esc } from "/static/grammar.js";
import { applyFilters, studiesOf, filtersInWords, metricsOf, displayValue } from "/static/dash/table.js";
import { proportionPapers } from "/static/dash/chart.js";
import * as scatter from "/static/dash/templates/scatter.js";
import * as forest from "/static/dash/templates/forest.js";
import * as rowsTable from "/static/dash/templates/rows_table.js";
import { dotStrip, bar, histogram, heatmap } from "/static/dash/templates/groups.js";
import { statCount, statAggregate, statShare, summaryTable } from "/static/dash/templates/numbers.js";

// template id (paperlens/dashboard_spec.py REGISTRY) → renderer
export const TEMPLATES = {
  scatter, forest, rows_table: rowsTable, dot_strip: { render: dotStrip }, bar: { render: bar }, histogram: { render: histogram },
  heatmap: { render: heatmap }, summary_table: { render: summaryTable }, stat_count: { render: statCount },
  stat_aggregate: { render: statAggregate }, stat_share: { render: statShare },
};

// every study a block draws on: reference, venue, link, and how many of the block's rows are its
function studiesHtml(rows) {
  const by = new Map();
  for (const r of rows) { const e = by.get(r.rec.paper) || { paper: r.paper, n: 0, unv: 0 }; e.n += 1; if (r.rec.status !== "verified") e.unv += 1; by.set(r.rec.paper, e); }
  const list = [...by.values()].sort((a, b) => String(a.paper.study || "").localeCompare(String(b.paper.study || "")));
  const doi = (d) => (d ? `<a href="https://doi.org/${encodeURIComponent(String(d).replace(/^https?:\/\/(dx\.)?doi\.org\//i, "")).replace(/%2F/g, "/")}" target="_blank" rel="noopener">DOI ↗</a>` : "");
  return `<table class="tp-table dash-studies-tb"><thead><tr><th>Study</th><th>Title</th><th>Journal</th><th>Rows</th><th>Verified</th><th></th></tr></thead><tbody>`
    + list.map((e) => `<tr><td>${esc(e.paper.study || "")}</td><td>${esc(e.paper.title || "")}</td><td>${esc(e.paper.journal || "")}</td>`
      + `<td class="tp-num">${e.n}</td><td class="tp-num">${e.n - e.unv} of ${e.n}</td><td>${doi(e.paper.doi)}</td></tr>`).join("") + `</tbody></table>`;
}

// What feeds a block: the dataset, how much of it the block uses, how those rows were extracted
// and checked, and where the data and the extraction recipe can be found.
function infoHtml(table, rows, block, filters) {
  const d = table.dataset, n = rows.length, k = studiesOf(rows);
  const count = (f) => { const m = new Map(); for (const r of rows) { const v = f(r); m.set(v, (m.get(v) || 0) + 1); } return [...m.entries()].sort((a, b) => b[1] - a[1]); };
  const status = Object.fromEntries(count((r) => r.rec.status));
  const models = count((r) => (r.rec.model && r.rec.model !== "imported" ? r.rec.model : "imported (model not recorded)"));
  const dates = rows.map((r) => r.rec.extracted_at).filter(Boolean).sort();
  const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");
  const line = (label, html) => (html ? `<tr><th>${label}</th><td>${html}</td></tr>` : "");
  return `<table class="dash-info-tb">`
    + line("Dataset", `<a href="/dataset?id=${encodeURIComponent(d.id)}">${esc(d.title || "Dataset")}</a> · version ${esc(String(d.version || 1))} · <span class="badge tier-${esc(d.credibility.tier || "ai_only")}">${esc(d.credibility.label || "")}</span>`)
    + line("Used here", `${n} of ${table.rows.length} rows (${esc(table.unit.label.toLowerCase())}), from ${k} of ${d.n_papers} paper${d.n_papers === 1 ? "" : "s"}`
      + (filters.length ? `<br/><span class="muted">kept: rows where ${esc(filtersInWords(filters, (c) => (table.col(c) || {}).label || c, (c, v) => (table.col(c) ? displayValue(table, table.col(c), v) : v)))}</span>` : ""))
    + line("Left out", d.left_out && d.left_out.rows ? `${d.left_out.rows} of ${d.left_out.of} ${esc(d.left_out.unit.toLowerCase())} rows in the dataset`
      + `${d.left_out.papers ? ` (${d.left_out.papers} paper${d.left_out.papers === 1 ? "" : "s"} entirely)` : ""} are not analysed: a row needs ${esc(d.left_out.needs)}. They remain in the dataset and its export.` : "")
    + line("Checked", `${status.verified || 0} of ${n} rows verified by a human${status.flagged ? `, ${status.flagged} flagged` : ""}${n - (status.verified || 0) - (status.flagged || 0) ? `, ${n - (status.verified || 0) - (status.flagged || 0)} not yet verified` : ""}`)
    + line("Extracted", models.map(([m, c]) => `${esc(m)} <span class="muted">(${c} row${c === 1 ? "" : "s"})</span>`).join(", ")
      + (dates.length ? `<br/><span class="muted">${dates[0] === dates[dates.length - 1] ? `on ${esc(day(dates[0]))}` : `between ${esc(day(dates[0]))} and ${esc(day(dates[dates.length - 1]))}`}</span>` : ""))
    + line("Last change", d.data_updated_at ? esc(day(d.data_updated_at)) : "")
    + line("Preset", d.preset ? `<a href="/preset?id=${encodeURIComponent(d.preset.id)}">${esc(d.preset.title)}</a> <span class="muted">(prompt, fields and evidence rules)</span>` : "")
    + line("Data", d.published_url ? `<a href="${esc(d.published_url)}" target="_blank" rel="noopener">published copy on GitHub ↗</a>` : `<span class="muted">not published on GitHub yet</span>`)
    + line("Cite", d.citation ? esc(d.citation) : "")
    + `</table>`;
}

const PCT_TEMPLATES = new Set(["scatter", "dot_strip", "bar", "histogram", "summary_table", "stat_aggregate"]);   // declare options.percent_scale
// tables: Map(unit id → hydrated table); ev: Map(unit id → evidence controller)
// edit (composer only): {note(text), option(id, value)} makes the preview editable in place and shows hints
// hideEmpty (the dashboard page): a block that has not enough data is not shown at all; the
// composer keeps it, with the reason, so its author can fix or remove it. Returns null then.
export function renderBlock(container, block, { tables, evidence, questions, dashFilters, edit, hideEmpty }) {
  if (hideEmpty && (block.sufficiency || {}).status === "insufficient") return null;
  const card = document.createElement("section");
  const suff = (block.sufficiency || {}).status;
  card.className = `dash-fig dash-${esc(block.type || "figure")}${(block.layout || {}).w === 2 ? " wide" : ""}${suff === "insufficient" ? " insufficient" : ""}`;
  card.dataset.block = block.id || "";
  // which question a block answers and its "intended message" are the author's working notes:
  // they live in the composer, a reader gets the title and the figure
  card.innerHTML = `<h3 class="dash-fig-h">${esc(block.title || "Untitled block")}</h3>`
    + (suff === "insufficient" ? `<p class="dash-warn">Not enough data: ${esc((block.sufficiency || {}).reason || "")}</p>` : "")
    + `<div class="dash-body"></div><div class="dash-info" hidden></div><p class="dash-fig-n"></p><div class="dash-studies" hidden></div>`
    + ((block.caveats || []).length ? `<ul class="dash-caveats">${block.caveats.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : "");
  container.appendChild(card);
  if (edit && edit.title) {                                  // in the editor the title is typed in place
    const h = card.querySelector(".dash-fig-h"), before = h.textContent;
    h.contentEditable = "plaintext-only"; h.spellcheck = false; h.title = "click to change the title";
    h.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); h.blur(); } if (ev.key === "Escape") { h.textContent = before; h.blur(); } });
    h.addEventListener("input", () => edit.title(h.textContent.replace(/\s+/g, " ").trim()));
  }
  const body = card.querySelector(".dash-body"), cap = card.querySelector(".dash-fig-n");
  const table = tables.get(block.unit);
  const tpl = TEMPLATES[block.template];
  if (!table || !tpl) { body.innerHTML = `<p class="nodata">${esc(!tpl ? `The "${block.template}" template is not available yet.` : "No data for this block's row unit.")}</p>`; return card; }
  let rows = applyFilters(table.rows, (block.transform || {}).filter, dashFilters);
  // Some studies report a share as 0–1, others as 0–100. Plotted together the first kind collapses
  // onto zero. Found per study; options.percent_scale puts them on the percent scale (the tooltip
  // still states the number as printed in the paper).
  const hints = [];
  for (const bnd of Object.values(block.bindings || {})) {
    const c = bnd && bnd.column && table.col(bnd.column);
    if (!c || !["number", "integer"].includes(c.type) || c.derived || !PCT_TEMPLATES.has(block.template)) continue;
    const prop = proportionPapers(rows, c.name);
    if (!prop) continue;
    if ((block.options || {}).percent_scale) {
      rows = rows.map((r) => {
        if (!prop.has(r.rec.paper) || typeof r.get(c.name) !== "number") return r;
        const w = Object.create(r), get = r.get;
        w.get = (n) => (n === c.name ? +(get(n) * 100).toFixed(8) : get(n));
        return w;
      });
    } else hints.push({ text: `${c.label}: ${prop.size} stud${prop.size === 1 ? "y reports" : "ies report"} it as a proportion (0–1), the others on 0–100, so those points collapse onto zero.`, option: "percent_scale", value: true, action: "Show proportions as percentages" });
  }
  // a filtered block says so: a reader must know the figure shows a subset
  const fs = (block.transform || {}).filter || [];
  if (fs.length) {
    const p = document.createElement("p"); p.className = "dash-filter";
    p.textContent = `Rows where ${filtersInWords(fs, (n) => (table.col(n) || {}).label || n, (n, v) => (table.col(n) ? displayValue(table, table.col(n), v) : v))}`;
    card.insertBefore(p, body);
  }
  // measures whose meaning differs per row: name the metric the plotted rows share, or warn
  const metric = {}; const mixed = [];
  for (const bnd of Object.values(block.bindings || {})) {
    const m = bnd && bnd.column ? metricsOf(table, rows, bnd.column) : null;
    if (!m || !m.list.length) continue;
    metric[bnd.column] = m;
    if (!m.single) mixed.push(`${(table.col(bnd.column) || {}).label}: ${m.list.map(([k, n]) => `${k} ${n}`).join(", ")}`);
  }
  if (mixed.length) {
    const by = (table.col(Object.values(metric)[0].by) || {}).label || "the metric";
    const w = document.createElement("p"); w.className = "dash-warn dash-mixed";
    w.textContent = `These rows mix different metrics, so the values are not on one scale (${mixed[0]}). Add a filter on “${by}” to compare like with like.`;
    card.insertBefore(w, body);
  }
  // suggestions about the scale, offered while composing (one click applies them)
  const showHints = (list) => {
    card.querySelectorAll(".dash-hintline").forEach((x) => x.remove());
    if (!edit) return;
    // one offer per option; while scales are mixed, the spread is no reason for a log scale
    const pct = list.filter((h) => h.option === "percent_scale");
    if (pct.length) list = [{ ...pct[0], text: pct.length > 1 ? pct[0].text.replace(/^[^:]+:/, `${pct.map((h) => h.text.split(":")[0]).join(", ")}:`) : pct[0].text }];
    list.forEach((h) => {
      const p = document.createElement("p"); p.className = "dash-hintline";
      p.innerHTML = `${esc(h.text)} <button type="button" class="dash-studies-t">${esc(h.action)}</button>`;
      p.querySelector("button").onclick = () => edit.option(h.option, h.value);
      card.insertBefore(p, body);
    });
  };
  const draw = () => {
    body.innerHTML = "";
    const out = tpl.render(body, block, { table, rows, ev: evidence.get(block.unit), metric, edit }) || { n: 0 };
    showHints([...hints, ...(out.hints || [])]);
    const used = out.used || rows;                          // the rows the template actually drew
    // the line under every block: how much data stands behind it, and which studies
    const k = studiesOf(used);
    cap.innerHTML = out.n && block.type !== "stat" ? `${out.n} row${out.n === 1 ? "" : "s"} from ${k} stud${k === 1 ? "y" : "ies"} · <button type="button" class="dash-studies-t" aria-expanded="false">show the studies</button>`
      + ` · <button type="button" class="dash-info-t" aria-expanded="false" title="the dataset behind this ${block.type === "table" ? "table" : "figure"}"><span class="dash-i" aria-hidden="true">i</span> about the data</button>` : "";
    const list = card.querySelector(".dash-studies"), btn = cap.querySelector(".dash-studies-t");
    list.hidden = true; list.innerHTML = "";
    // ⓘ swaps the figure for a description of the data that feeds it, and back
    const info = card.querySelector(".dash-info"), ibtn = cap.querySelector(".dash-info-t");
    info.hidden = true; body.hidden = false;
    if (ibtn) ibtn.onclick = () => {
      const show = info.hidden;
      if (show) info.innerHTML = infoHtml(table, used, block, fs);
      info.hidden = !show; body.hidden = show; ibtn.setAttribute("aria-expanded", String(show));
      ibtn.innerHTML = show ? "← back to the figure" : `<span class="dash-i" aria-hidden="true">i</span> about the data`;
    };
    if (btn) btn.onclick = () => {
      if (!list.innerHTML) list.innerHTML = studiesHtml(used);
      list.hidden = !list.hidden; btn.setAttribute("aria-expanded", String(!list.hidden));
      btn.textContent = list.hidden ? "show the studies" : "hide the studies";
    };
    return out;
  };
  const out = draw();
  // nothing to draw from the data → no card. Not when the READER emptied it with "verified rows
  // only" / "exclude flagged": then the card stays and says that no rows match.
  const filtered = !!(dashFilters && (dashFilters.verified_only || dashFilters.exclude_flagged));
  if (hideEmpty && !out.n && block.type !== "stat" && !filtered) { card.remove(); return null; }
  if (out.marks && out.marks.length && out.marks.length <= 150) evidence.get(block.unit).prefetch(out.marks);
  // charts are drawn at the width of their card: draw again when that width changes (window
  // resize, a card that was laid out late), so the text never ends up scaled
  if (block.type !== "stat" && typeof ResizeObserver === "function") {
    let w = Math.round(body.getBoundingClientRect().width), timer = null;
    const ro = new ResizeObserver(() => {
      if (!card.isConnected) { ro.disconnect(); return; }
      const now = Math.round(body.getBoundingClientRect().width);
      if (!now || Math.abs(now - w) < 8) return;
      w = now; clearTimeout(timer); timer = setTimeout(draw, 120);
    });
    ro.observe(body);
  }
  return card;
}
