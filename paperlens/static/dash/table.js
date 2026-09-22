// The analysis table of a dataset, hydrated for the dashboard templates: typed columns and
// row objects that know their record, paper and provenance. Evidence is NOT in here: it is
// fetched per cell by evidence.js. Mirrors paperlens/analysis_table.py.
import { api } from "/static/api.js";

// How a coded value is written on an axis, in a legend, a tooltip or a table: the label the
// dashboard gives it (spec.value_labels, proposed by the planner and editable), else a code like
// "advice_first" is at least un-snaked ("Advice first"). Filters keep using the coded value.
const CODE = /^[a-z][a-z0-9]*(_[a-z0-9]+)+$/;
export const MANY = new Set(["multi", "list"]);             // column types whose cell holds several values, joined by ", "
const humanise = (v) => (CODE.test(v) ? (v.charAt(0).toUpperCase() + v.slice(1)).replace(/_/g, " ") : v);
export function displayValue(table, col, v) {
  if (v == null || typeof v === "number") return v;
  if (Array.isArray(v)) return v.map((x) => displayValue(table, col, x));
  const own = (table.valueLabels || {})[col.name];
  if (own && own[String(v)] != null) return own[String(v)];
  if (typeof v === "string" && (col.roles || []).includes("dimension")) {
    return MANY.has(col.type) && v.includes(", ") ? v.split(", ").map((x) => (own && own[x] != null ? own[x] : humanise(x))).join(", ") : humanise(v);
  }
  return v;
}

// source: a dataset id (its live data) · {dataset, release} (a frozen release of it) ·
// {dashboard, view} (what that dashboard shows: the pinned release of the published page, or the
// owner's live draft). The table remembers its source, so evidence is fetched from the same place.
export const sourceOf = (s) => (typeof s === "string" ? { dataset: s } : s);
export async function loadTable(source, unit) {
  const src = sourceOf(source);
  const t = src.dashboard ? await api.dashboardTable(src.dashboard, unit || null, src.view)
    : await api.analysisTable(src.dataset, unit || null, src.release);
  const colIndex = new Map(t.columns.map((c, i) => [c.name, i]));
  const table = { ...t, source: src, colIndex, valueLabels: {}, col: (name) => t.columns[colIndex.get(name)] || null };
  table.rows = t.rows.map((w, i) => {
    const rec = t.records[w.r];
    const raw = (name) => (colIndex.has(name) ? w.v[colIndex.get(name)] : undefined);
    return {
      i, rec, paper: t.papers[rec.paper], path: w.p, raw,
      get: (name) => (colIndex.has(name) ? displayValue(table, t.columns[colIndex.get(name)], raw(name)) : undefined),
      corrected: (name) => !!(w.c && colIndex.has(name) && w.c.includes(colIndex.get(name))),
    };
  });
  return table;
}
// the dashboard's own names for variables ("Paper title" → "Paper"): everything that prints a
// column label (axes, legends, tooltips, table heads, the composer's menus) then uses them
export function setColumnLabels(tables, labels) {
  for (const t of tables.values()) for (const c of t.columns) { if (c.label0 === undefined) c.label0 = c.label; c.label = (labels || {})[c.name] || c.label0; }
}
// the dashboard's labels apply to every table it draws from
export function setValueLabels(tables, labels) { for (const t of tables.values()) t.valueLabels = labels || {}; }

// block.transform.filter = [{column, op, value}]; dashFilters = {verified_only, exclude_flagged}
const same = (a, b) => a === b || String(a) === String(b);
const OPS = {
  eq: same, ne: (a, b) => !same(a, b),
  in: (a, b) => (Array.isArray(b) ? b : [b]).some((x) => same(a, x)),
  gt: (a, b) => a != null && a > b, gte: (a, b) => a != null && a >= b,
  lt: (a, b) => a != null && a < b, lte: (a, b) => a != null && a <= b,
  between: (a, b) => a != null && Array.isArray(b) && a >= b[0] && a <= b[1],
  not_null: (a) => a != null && a !== "",
};
export function applyFilters(rows, filters, dashFilters) {
  const fs = (filters || []).filter((f) => f && f.column && OPS[f.op]);
  const df = dashFilters || {};
  return rows.filter((r) => {
    if (df.verified_only && r.rec.status !== "verified") return false;
    if (df.exclude_flagged && r.rec.status === "flagged") return false;
    return fs.every((f) => OPS[f.op](r.raw(f.column), f.value));      // on the coded value
  });
}

const sum = (v) => v.reduce((a, b) => a + b, 0);
const AGG = {
  count: (v) => v.length, sum,
  mean: (v) => (v.length ? sum(v) / v.length : null),
  median: (v) => { if (!v.length) return null; const s = [...v].sort((a, b) => a - b), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; },
  min: (v) => (v.length ? Math.min(...v) : null), max: (v) => (v.length ? Math.max(...v) : null),
};
export const AGGS = Object.keys(AGG);
// groups keep their member rows, so an aggregated mark can still show where it came from
export function groupBy(rows, dims, measure, agg) {
  const groups = new Map();
  for (const r of rows) {
    const key = dims.map((d) => { const v = r.get(d); return v == null || v === "" ? "—" : String(v); });
    const k = JSON.stringify(key);
    if (!groups.has(k)) groups.set(k, { key, rows: [] });
    groups.get(k).rows.push(r);
  }
  return [...groups.values()].map((g) => {
    const vals = measure ? g.rows.map((r) => r.get(measure)).filter((v) => typeof v === "number") : [];
    const useRows = agg === "count" || !measure;
    return { ...g, value: (AGG[agg] || AGG.count)(useRows ? g.rows : vals), n: g.rows.length };
  });
}
export const OP_WORDS = { eq: "is", ne: "is not", in: "is", gt: "above", gte: "at least", lt: "below", lte: "at most", between: "between", not_null: "has a value" };
// "Study is Qazi et al. (2026) and Mean human at least 100": the filters of a block in words
export function filtersInWords(filters, labelOf, valueOf = (c, v) => v) {
  return (filters || []).map((f) => `${labelOf(f.column)} ${OP_WORDS[f.op] || f.op}`
    + (f.op === "not_null" ? "" : ` ${Array.isArray(f.value) ? f.value.map((v) => valueOf(f.column, v)).join(f.op === "between" ? " and " : " or ") : valueOf(f.column, f.value)}`)).join(" and ");
}
// What the numbers of a measure mean can differ per row (accuracy, seconds, a score). For the
// rows a block plots: the one metric they share, or the mix with its counts.
export function metricsOf(table, rows, colName) {
  const c = table.col(colName);
  if (!c || !c.unit_by) return null;
  const counts = new Map();
  for (const r of rows) { if (r.get(colName) == null) continue; const m = r.get(c.unit_by); const k = m == null || m === "" ? "unspecified" : String(m); counts.set(k, (counts.get(k) || 0) + 1); }
  const list = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  return { by: c.unit_by, list, single: list.length === 1 ? list[0][0] : null };
}
export const studiesOf = (rows) => new Set(rows.map((r) => r.rec.paper)).size;
