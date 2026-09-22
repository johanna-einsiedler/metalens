// Shared D3 drawing helpers for the dashboard templates (D3 is the vendored classic global).
// Colour comes only from CSS classes backed by theme tokens (.mark, .series-N, .stroke-N).
import { esc } from "/static/grammar.js";

export const d3 = window.d3;
export const fmt = (v) => (v == null || v === "" ? "—" : typeof v === "number"
  ? (Number.isInteger(v) ? String(v) : Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")) : String(v));
export const seriesCls = (i) => `series-${(i % 8) + 1}`;
export const strokeCls = (i) => `stroke-${(i % 8) + 1}`;

export function nodata(root, msg) { root.innerHTML = `<p class="nodata">${esc(msg || "No rows match this block.")}</p>`; return { n: 0 }; }
export function svgEl(root, W, H, label) {
  return d3.select(root).append("svg").attr("class", "fig-svg").attr("role", "group").attr("aria-label", label || "chart")
    .attr("viewBox", `0 0 ${W} ${H}`).attr("preserveAspectRatio", "xMinYMin meet").style("max-width", `${W}px`);   // never blown up
}
export function legend(root, cats, title) {
  if (cats.length < 2) return;
  const div = document.createElement("div"); div.className = "fig-legend";
  div.innerHTML = (title ? `<span class="lg-title">${esc(title)}</span>` : "")
    + cats.map((c, i) => `<span class="lg"><span class="sw sw-${(i % 8) + 1}"></span>${esc(String(c))}</span>`).join("");
  root.appendChild(div);
}
export function xTitle(svg, W, H, t) { if (t) svg.append("text").attr("class", "ax-title").attr("x", W / 2).attr("y", H - 4).attr("text-anchor", "middle").text(t); }
export function yTitle(svg, H, t) { if (t) svg.append("text").attr("class", "ax-title").attr("transform", "rotate(-90)").attr("x", -H / 2).attr("y", 13).attr("text-anchor", "middle").text(t); }
export function trunc(nchars) {
  return function () {
    const t = d3.select(this), full = t.text();
    if (full.length > nchars) { t.text(full.slice(0, nchars - 1) + "…"); t.append("title").text(full); }
  };
}
// the drawing width follows the card, so text keeps its size in wide and narrow blocks alike
// the drawing width IS the displayed width, so text and marks keep one size in every card
// (blocks.js redraws a block when its card changes width)
export const widthOf = (root, min = 300) => Math.max(min, Math.round((root.getBoundingClientRect().width || 0)) || 640);
// a label binding names one column or several ({columns: [...]}): "Choi & Schwarcz (2025) · Few shot"
export function labelOf(row, binding, fallback = "_study") {
  const cols = binding && Array.isArray(binding.columns) && binding.columns.length ? binding.columns : [(binding && binding.column) || fallback];
  return cols.map((c) => row.get(c)).filter((v) => v != null && v !== "").map(String).join(" · ") || String(row.paper.study || "");
}
// the label of a column, with the metric its plotted rows share ("Mean human (Accuracy)") or the
// plain word "mixed metrics" when they do not (blocks.js computes ctx.metric per bound column)
export const colLabel = (ctx, name) => {
  const c = name && ctx.table.col(name); if (!c) return name || "";
  const m = ctx.metric && ctx.metric[name];
  return m ? `${c.label} (${m.single || "mixed metrics"})` : c.label;
};

// Every mark is traceable: hover or focus shows where it comes from, click or Enter pins the
// detail. `markOf(datum)` → {rows:[row…], label, values:[{slot, col, label, value}]}. Marks whose
// rows are all unverified are drawn hollow, so trust is visible before any hover.
export function wireMark(sel, ctx, markOf) {
  sel.attr("tabindex", 0).attr("role", "img").classed("dash-mark", true)
    .classed("unverified", (d) => { const m = markOf(d); return m.rows.length > 0 && m.rows.every((r) => r.rec.status !== "verified"); })
    .attr("aria-label", (d) => { const m = markOf(d); return `${m.label}: ${m.values.map((v) => `${v.label} ${fmt(v.value)}`).join(", ")}`; })
    .on("mouseenter focus", function (ev, d) { d3.select(this).classed("hover", true); ctx.ev.hover(markOf(d), ev, this); })
    .on("mousemove", (ev) => ctx.ev.move(ev))
    .on("mouseleave blur", function () { d3.select(this).classed("hover", false); ctx.ev.leave(); })
    .on("click", (ev, d) => ctx.ev.pin(markOf(d)))
    .on("keydown", (ev, d) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ctx.ev.pin(markOf(d)); } });
}

// A numeric scale over the values of an axis: logarithmic when asked for and possible (every
// value above zero), else linear. `extra` = values the axis must also cover (a reference line).
export function numScale(values, log, extra = []) {
  const all = values.concat(extra.filter((v) => typeof v === "number"));
  const useLog = !!log && all.every((v) => v > 0);
  return { scale: (useLog ? d3.scaleLog() : d3.scaleLinear()).domain(d3.extent(all)).nice(), log: useLog };
}
export const logTicks = (axis, isLog, n) => (isLog ? axis.ticks(n, "~g") : axis.ticks(n));
// Would a log scale help? Only when it can be drawn (all values > 0), the values span at least
// two orders of magnitude, and on a linear axis most points pile up at the low end.
export function logWorthIt(values) {
  if (values.length < 8 || values.some((v) => !(v > 0))) return false;
  const lo = Math.min(...values), hi = Math.max(...values);
  if (hi / lo < 100) return false;
  const low = values.filter((v) => v <= lo + (hi - lo) * 0.1).length;
  return low / values.length >= 0.6;
}
// Studies that report a quantity as a proportion (all their values within 0–1) next to studies
// that report it on 0–100: the papers (indices) on the proportion scale, or null when not mixed.
export function proportionPapers(rows, col) {
  const by = new Map();
  for (const r of rows) { const v = r.get(col); if (typeof v === "number") { if (!by.has(r.rec.paper)) by.set(r.rec.paper, []); by.get(r.rec.paper).push(v); } }
  const prop = new Set(); let pct = 0;
  for (const [paper, vals] of by) {
    const lo = Math.min(...vals), hi = Math.max(...vals);
    if (lo >= 0 && hi <= 1 && vals.some((v) => !Number.isInteger(v))) prop.add(paper);
    else if (lo >= 0 && hi > 1 && hi <= 100) pct += 1;
  }
  return prop.size && pct ? prop : null;
}
