// metalens-dash.js — helpers for a dashboard you write yourself over a Metalens RELEASE.
//
// A release is a folder of static files (release.json, tables/<layout>.json, evidence.json). This
// module reads them — no Metalens server involved — and gives your own D3 marks what the dashboards
// built inside Metalens have: hover or focus a mark and see where the number comes from (paper,
// verification status, the quoted passage with the number highlighted, table / row / page); click to
// pin the full evidence under the figure.
//
//   const release = await loadRelease("data/my-dataset-v3");
//   const table   = await release.table();                     // default row layout, hydrated rows
//   const tips    = createTips({ release, panel: "#evidence" });
//   tips.mark(selection, (d) => ({ title, subtitle, values: [{label, value}], rows: [{unit, row}],
//                                 columns: [{column: "Avg_Perf_Human", label: "human alone", value: d.human}, …] }));
//
// Rows of other sources (no release behind them) work too: leave out `rows` and pass `note`.
const d3 = window.d3;

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const fmt = (v, digits = 3) => (v == null || Number.isNaN(v) ? "—" : typeof v === "number"
  ? (Math.abs(v) >= 1000 ? d3.format(",.0f")(v) : String(+v.toFixed(digits))) : String(v));
export const pct = (v, digits = 0) => (v == null ? "—" : `${(100 * v).toFixed(digits)}%`);
export const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "");

// ── the release ─────────────────────────────────────────────────────────────────────────────────
export async function loadRelease(base) {
  const get = async (p) => { const r = await fetch(`${base}/${p}`, { cache: "no-cache" }); if (!r.ok) throw new Error(`${p}: ${r.status}`); return r.json(); };
  const [meta, evidence] = await Promise.all([get("release.json"), get("evidence.json")]);
  const tables = new Map();
  async function table(unitId) {
    const unit = meta.units.find((u) => (unitId ? u.id === unitId : u.default)) || meta.units[0];
    if (!tables.has(unit.id)) {
      const t = await get(unit.file), at = new Map(t.columns.map((c, i) => [c.name, i]));
      t.col = (n) => t.columns[at.get(n)] || null;
      t.rows = t.rows.map((w, i) => ({ i, rec: t.records[w.r], paper: t.papers[t.records[w.r].paper], path: w.p,
        get: (n) => (at.has(n) ? w.v[at.get(n)] : undefined), corrected: (n) => !!(w.c && at.has(n) && w.c.includes(at.get(n))) }));
      tables.set(unit.id, t);
    }
    return tables.get(unit.id);
  }
  // the evidence of one cell; a computed column (Hedges' g …) rests on its inputs
  function evidenceOf(unit, row, column) {
    const t = tables.get(unit), c = t && t.col(column);
    if (c && c.derived) return { kind: "derived", formula: c.derived.text, inputs: c.derived.inputs.map((n) => ({ column: n, label: (t.col(n) || {}).label || n, ...evidenceOf(unit, row, n) })) };
    const cell = ((evidence.cells[unit] || {})[String(row)] || {})[column];
    return cell ? { kind: cell.k, items: cell.i.map((k) => evidence.items[k]), was: cell.was } : { kind: "none", items: [] };
  }
  return { base, meta, table, evidenceOf, unitDefault: (meta.units.find((u) => u.default) || meta.units[0]).id };
}

// ── where a quote sits, in plain words: "Table 2, row “Few shot”, column “Accuracy”, p. 20" ───────
// `source` is the label the extractor wrote (a table, a figure legend, a section); the row and column
// come from the table layout when the release carries it (evidence items' `context`).
function whereText(kind, it) {
  const src = (it.source || "").trim(), ctx = it.context || {};
  const row = (ctx.row_label || "").trim(), col = (ctx.cells || []).map((c) => c.header).filter(Boolean);
  const parts = [src, row ? `row “${row.slice(0, 40)}”` : (kind === "row" && /^table/i.test(src) ? "one row" : ""),
    col.length ? `column${col.length > 1 ? "s" : ""} ${col.slice(0, 3).map((c) => `“${c}”`).join(", ")}` : "", it.page != null ? `p. ${it.page}` : ""];
  return parts.filter(Boolean).join(", ") + (kind === "entry" ? " (quoted for the whole entry)" : "");
}
// the plotted numbers, marked inside the quote
const numberRe = (v) => { const forms = [...new Set([String(v), String(v).replace(/^0\./, "."), v.toFixed(1), v.toFixed(2), v.toFixed(3)])];
  return new RegExp(`(^|[^0-9.])(${forms.map((f) => f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![0-9])`); };
const hasNumber = (text, v) => typeof v === "number" && numberRe(v).test(String(text || ""));
function markNumbers(text, numbers) {
  let html = esc(text);
  for (const v of numbers) if (typeof v === "number") html = html.replace(numberRe(v), "$1<mark>$2</mark>");
  return html;
}
const STATUS = { verified: "verified by a human", unverified: "not yet verified", flagged: "flagged by a reviewer" };

// ── tooltip + pinned evidence ───────────────────────────────────────────────────────────────────
export function createTips({ release, panel }) {
  let tip = document.querySelector("body > .ml-tip");               // one tooltip for the page, however often figures are redrawn
  if (!tip) { tip = document.createElement("div"); tip.className = "ml-tip"; tip.hidden = true; document.body.appendChild(tip); }
  const host = typeof panel === "string" ? document.querySelector(panel) : panel;
  const place = (x, y) => {
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight, pad = 14;
    tip.style.left = `${Math.max(8, Math.min(x + pad, innerWidth - w - 8))}px`;
    tip.style.top = `${y + pad + h > innerHeight - 8 ? Math.max(8, y - h - pad) : y + pad}px`;
  };
  // The quotes behind a mark. `columns` = [{column, label, value}] in the order the values are shown.
  // A quote that supports several of them (one table row) appears once, SAYS which ones, and only
  // THEIR numbers are highlighted in it, so "56" in the AI-alone row is never read as the team's score.
  function quotes(desc, max) {
    const seen = new Map();
    const add = (ev, col) => {
      if (!ev) return;
      if (ev.kind === "derived") { ev.inputs.forEach((i) => add(i, { column: i.column, label: i.label })); return; }
      for (const it of (ev.items || []).slice(0, 1)) {
        const key = `${ev.kind}|${it.page}|${it.source}|${it.snippet}`;
        if (!seen.has(key)) seen.set(key, { ev, it, cols: [] });
        if (!seen.get(key).cols.some((c) => c.column === col.column)) seen.get(key).cols.push(col);
      }
    };
    const cols = (desc.columns || []).map((c) => (typeof c === "string" ? { column: c, label: c } : c));
    for (const r of desc.rows || []) for (const c of cols) add(release.evidenceOf(r.unit || release.unitDefault, r.row, c.column), c);
    const len = max > 3 ? 600 : 220, found = new Set();
    // A quote is named after the values that are actually IN it. A row-level quote is stored for every
    // value of its row, but if it only holds the AI's score it must not pass as the source of the team's.
    const out = [...seen.values()].slice(0, max).map(({ ev, it, cols: cs }) => {
      const inIt = cs.filter((c) => hasNumber(it.snippet, c.value)); inIt.forEach((c) => found.add(c.column));
      const names = (inIt.length ? inIt : cs.filter((c) => typeof c.value !== "number")).map((c) => c.label);
      return `<div class="ml-ev"><span class="ml-where">${names.length ? `<b>${esc(names.join(", "))}</b> · ` : ""}${esc(whereText(ev.kind, it))}</span>`
        + `<div class="ml-quote">“${markNumbers((it.snippet || "").slice(0, len), inIt.map((c) => c.value))}${(it.snippet || "").length > len ? "…" : ""}”</div></div>`;
    });
    const missing = cols.filter((c) => typeof c.value === "number" && !found.has(c.column));
    if (missing.length && seen.size) out.push(`<div class="ml-where">Not in the quoted passages: ${esc(missing.map((c) => `${c.label} ${fmt(c.value)}`).join(", "))}</div>`);
    return out.join("");
  }
  function html(desc, full) {
    const q = desc.rows && desc.rows.length ? quotes(desc, full ? 12 : 3) : "";
    return `<div class="ml-vals">${(desc.values || []).map((v) => `<span class="ml-val"><span class="ml-slot">${esc(v.label)}</span> ${esc(v.text ?? fmt(v.value))}</span>`).join("")}</div>`
      + `<div class="ml-title">${esc(desc.title || "")}</div>${desc.subtitle ? `<div class="ml-sub">${esc(desc.subtitle)}</div>` : ""}`
      + (desc.status ? `<div class="ml-meta"><span class="ml-pill ml-${esc(desc.status)}">${esc(STATUS[desc.status] || desc.status)}</span>${desc.detail ? ` · ${esc(desc.detail)}` : ""}</div>` : desc.detail ? `<div class="ml-meta">${esc(desc.detail)}</div>` : "")
      + (desc.formula ? `<div class="ml-where">Computed: ${esc(desc.formula)}</div>` : "")
      + (q || (desc.rows && desc.rows.length ? `<div class="ml-where">No evidence recorded for these values.</div>` : ""))
      + (desc.note ? `<div class="ml-note">${esc(desc.note)}</div>` : "")
      + (!full && desc.rows && desc.rows.length ? `<div class="ml-hint">click to pin the evidence</div>` : "");
  }
  const show = (desc, x, y) => { tip.innerHTML = html(desc, false); place(x, y); };
  const hide = () => { tip.hidden = true; };
  function pin(desc) {
    if (!host) return;
    host.innerHTML = `<div class="ml-panel"><button type="button" class="ml-x" aria-label="close">✕</button>${html(desc, true)}</div>`;
    host.querySelector(".ml-x").onclick = () => { host.innerHTML = ""; };
    host.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  // make the marks of a d3 selection traceable
  function mark(sel, describe) {
    sel.attr("tabindex", 0).attr("role", "img").classed("ml-mark", true)
      .attr("aria-label", (d) => { const x = describe(d); return `${x.title}: ${(x.values || []).map((v) => `${v.label} ${v.text ?? fmt(v.value)}`).join(", ")}`; })
      .on("mouseenter", function (ev, d) { d3.select(this).classed("hover", true); show(describe(d), ev.clientX, ev.clientY); })
      .on("mousemove", (ev) => place(ev.clientX, ev.clientY))
      .on("focus", function (ev, d) { const b = this.getBoundingClientRect(); show(describe(d), b.left + b.width / 2, b.top); })
      .on("mouseleave blur", function () { d3.select(this).classed("hover", false); hide(); })
      .on("click", (ev, d) => pin(describe(d)))
      .on("keydown", (ev, d) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pin(describe(d)); } });
    return sel;
  }
  return { mark, show, hide, pin, place };
}

// ── small chart helpers ─────────────────────────────────────────────────────────────────────────
export function svgIn(root, W, H, label) {
  return d3.select(root).append("svg").attr("class", "fig").attr("viewBox", `0 0 ${W} ${H}`).attr("role", "group").attr("aria-label", label || "chart")
    .style("max-width", `${W}px`).style("width", "100%").style("height", "auto").style("display", "block").style("overflow", "visible");
}
export const widthOf = (root, min = 320) => Math.max(min, Math.round(root.getBoundingClientRect().width) || 720);
// a stable pseudo-random number in [0, 1) from a string: jitter that does not dance between redraws
export function hash01(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return ((h >>> 0) % 10000) / 10000; }
