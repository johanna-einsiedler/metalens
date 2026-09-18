// The review UI's view of a preset: ONE normalised model built from the document payload
// (DATA.spec, which the server always provides — exact for format-2 rows, inferred for
// legacy ones), plus an evidence index keyed by record + record-relative path.
// Everything the renderers need — declared fields, tabs, children, confidence groups,
// titles, which evidence belongs to which cell — comes from here, never from
// field_defs.sub_views / field_types / render_hints directly.
import { esc, formatKey } from "/static/grammar.js";

export const SCALAR_TYPES = new Set(["string", "text", "integer", "number", "boolean", "enum", "multi", "list"]);
const RESERVED = new Set(["evidence", "confidence", "extraction_confidence"]);

// ── the view model ────────────────────────────────────────────────────────────
export function viewModel(DATA) {
  const spec = DATA.spec || inferSpec(DATA);
  const legacy = !!((DATA.field_defs && DATA.field_defs.legacy) || !DATA.spec);
  const ent = spec.entries || {};
  const groups = {};
  for (const g of (spec.confidence && spec.confidence.groups) || []) groups[g.id] = g;
  const vm = {
    spec, legacy,
    entries: { key: ent.key || "records", label: ent.label || "Entry", id_field: ent.id_field || null,
               title: ent.title || null, cardinality: ent.cardinality || "many",
               evidence: ent.evidence || "row",
               fields: ent.fields || [], children: ent.children || [] },
    paper: { fields: (spec.paper && spec.paper.fields) || [] },
    groups,
    levels: (spec.confidence && spec.confidence.levels) || ["high", "medium", "low"],
    triage: (spec.display && spec.display.triage) || "declaration",
    entriesLayout: (spec.display && spec.display.entries) || "cards",
    gridRows: (spec.display && spec.display.grid_rows) || "entries",
    paperPanel: (spec.display && spec.display.paper_panel) || "open",
    citationFlash: !(spec.display && spec.display.citation_flash === false),   // false: only the located value blinks
    locate: (spec.display && spec.display.locate) || {},   // table field → how a clicked number is pinpointed
    tabs: [],
    fieldIndex: new Map(),
  };
  for (const f of vm.paper.fields) vm.fieldIndex.set(f.name, { scope: "paper", field: f, child: null });
  for (const f of vm.entries.fields) vm.fieldIndex.set(f.name, { scope: "entry", field: f, child: null });
  for (const ch of vm.entries.children) {
    vm.fieldIndex.set(ch.key, { scope: "child_key", field: null, child: ch });
    for (const f of ch.fields || []) vm.fieldIndex.set(f.name, { scope: "child", field: f, child: ch.key });
  }
  vm.tabs = completeTabs(vm, (spec.display && spec.display.tabs) || []);
  return vm;
}

// A spec for a document the server could not describe (no schema row, no records to infer
// from): every key becomes a plain field so nothing is hidden.
function inferSpec(DATA) {
  const names = [];
  for (const r of DATA.records || []) for (const k of Object.keys(r.field_values || {}))
    if (!RESERVED.has(k) && !names.includes(k)) names.push(k);
  return { entries: { key: "records", label: "Entry", fields: names.map((n) => ({ name: n, type: "string", evidence: "value" })), children: [] },
           paper: { fields: [] }, confidence: { groups: [], levels: ["high", "medium", "low"] }, display: {} };
}

// Every declared entry field / child ends up in exactly one tab: the declared one, else an
// auto "Other" tab appended last. Each tab also knows which confidence groups its fields
// belong to (where the badges go).
export function completeTabs(vm, declared) {
  const placeable = [...vm.entries.fields.map((f) => f.name), ...vm.entries.children.map((c) => c.key)];
  const placed = new Set();
  const tabs = declared.map((t) => {
    const fields = (t.fields || []).filter((n) => placeable.includes(n) && !placed.has(n));
    fields.forEach((n) => placed.add(n));
    return { id: t.id, label: t.label || formatKey(t.id), fields };
  }).filter((t) => t.fields.length);
  const rest = placeable.filter((n) => !placed.has(n));
  if (rest.length) tabs.push({ id: tabs.length ? "_other" : "details", label: tabs.length ? "Other" : "Details", fields: rest });
  for (const t of tabs) {
    const gids = new Set();
    for (const n of t.fields) {
      const info = vm.fieldIndex.get(n);
      if (info && info.field && info.field.confidence) gids.add(info.field.confidence);
      if (info && info.child) for (const f of info.child.fields || []) if (f.confidence) gids.add(f.confidence);
    }
    t.groups = [...gids].filter((g) => vm.groups[g] && vm.groups[g].scope !== "paper");
  }
  return tabs;
}

// ── titles ────────────────────────────────────────────────────────────────────
function fillTemplate(tpl, obj, index) {
  if (!tpl) return "";
  const out = tpl.replace(/\{(#|[A-Za-z_][A-Za-z0-9_]*)\}/g, (_, k) => {
    if (k === "#") return String(index + 1);
    const v = (obj || {})[k];
    return v === null || v === undefined || typeof v === "object" ? "" : String(v);
  }).replace(/\s+/g, " ").trim();
  return out.replace(/^[\s–—:·-]+|[\s–—:·-]+$/g, "");
}
export function entryTitle(vm, rec, i) {
  const fv = rec.field_values || {};
  const idx = rec.entry_index != null ? rec.entry_index : i;
  const t = fillTemplate(vm.entries.title, fv, idx);
  if (t) return t;
  if (vm.entries.id_field && fv[vm.entries.id_field] != null && fv[vm.entries.id_field] !== "")
    return `${vm.entries.label} ${fv[vm.entries.id_field]}`;
  if (vm.legacy && (fv.table_id || fv.title)) return String(fv.table_id || fv.title);
  return `${vm.entries.label} ${idx + 1}`;
}
export function childTitle(child, row, k) {
  return fillTemplate(child.title, row, k) || `${child.label || child.key} ${k + 1}`;
}

// ── paths & evidence ──────────────────────────────────────────────────────────
// A record-relative path ("n", "records[2].es", "records[2]", "loadings[4]", "") from an
// absolute evidence field_path; null when the path is not about this document's entries.
export function relPath(vm, fieldPath) {
  if (!fieldPath) return null;
  const m = String(fieldPath).trim().match(/^([A-Za-z_][A-Za-z0-9_]*)(?:\._table)?\[(\d+)\]\.?(.*)$/);
  if (!m) return null;
  return { key: m[1], entryIndex: +m[2], rel: (m[3] || "").replace(/\._table\[/g, "[") };
}
export function parseRel(vm, rel) {
  // → {childKey, childIndex, tableKey, rowIndex, field}
  const out = { childKey: null, childIndex: null, tableKey: null, rowIndex: null, field: null };
  if (!rel) return out;
  const segs = [];
  rel.replace(/([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?/g, (_, n, i) => { segs.push([n, i === undefined ? null : +i]); return ""; });
  if (!segs.length) return out;
  let [name, idx] = segs[0];
  let info = vm.fieldIndex.get(name);
  const isChild = info ? info.scope === "child_key" : (idx !== null && segs.length > 1);
  let rest = segs.slice(1);
  if (isChild) {
    out.childKey = name; out.childIndex = idx;
    if (!rest.length) return out;
    [name, idx] = rest[0]; rest = rest.slice(1); info = vm.fieldIndex.get(name);
  }
  if (idx !== null || (info && info.field && info.field.type === "table")) { out.tableKey = name; out.rowIndex = idx; return out; }
  out.field = name;
  return out;
}

// Index every evidence item by what it can be attached to. Orphans (no record, no
// resolvable entry) are listed separately and NEVER attached to a cell.
export function indexEvidence(DATA, vm) {
  const EV = { byRecord: new Map(), exact: new Map(), rowCites: new Map(), tableCites: new Map(),
               entryCites: new Map(), orphans: [], hasRect: [] };
  const byIndex = new Map((DATA.records || []).map((r) => [r.entry_index, r]));
  const push = (map, key, i) => { if (!map.has(key)) map.set(key, []); map.get(key).push(i); };
  (DATA.evidence || []).forEach((ev, i) => {
    EV.hasRect[i] = Array.isArray(ev.rect) && ev.rect.length > 0;
    let rec = ev.record_id ? (DATA.records || []).find((r) => r.id === ev.record_id) : null;
    const rp = relPath(vm, ev.field_path);
    if (!rec && rp && byIndex.has(rp.entryIndex)) rec = byIndex.get(rp.entryIndex);
    if (!rec && ev.entry_index != null && byIndex.has(ev.entry_index)) rec = byIndex.get(ev.entry_index);
    if (!rec) { EV.orphans.push(i); return; }
    const rel = rp ? rp.rel : "";
    push(EV.byRecord, rec.id, i);
    if (rel === "") { push(EV.entryCites, rec.id, i); return; }
    const p = parseRel(vm, rel);
    if (p.childKey !== null && p.childIndex !== null && p.field === null && p.tableKey === null) {
      push(EV.rowCites, `${rec.id}|${p.childKey}[${p.childIndex}]`, i); return;
    }
    if (p.childKey !== null && p.childIndex === null) { push(EV.tableCites, `${rec.id}|${p.childKey}`, i); return; }
    if (p.tableKey !== null) {
      const base = p.childKey !== null ? `${p.childKey}[${p.childIndex}].${p.tableKey}` : p.tableKey;
      push(p.rowIndex === null ? EV.tableCites : EV.rowCites, `${rec.id}|${base}${p.rowIndex === null ? "" : `[${p.rowIndex}]`}`, i);
      return;
    }
    push(EV.exact, `${rec.id}|${rel}`, i);
  });
  return EV;
}

// What supports a given cell: {kind: exact|row|table|entry, ids} or null. `path` is the
// record-relative data-path the grammar emits ("records[2].es", "n", "loadings[4].loading").
export function evidenceFor(EV, vm, recId, path) {
  const ex = EV.exact.get(`${recId}|${path}`);
  if (ex) return { kind: "exact", ids: ex };
  // walk up: child row → child table → table row → table → entry
  const p = parseRel(vm, path);
  const tries = [];
  if (p.tableKey !== null && p.rowIndex !== null) {
    const base = p.childKey !== null ? `${p.childKey}[${p.childIndex}].${p.tableKey}` : p.tableKey;
    tries.push([`${recId}|${base}[${p.rowIndex}]`, "row"], [`${recId}|${base}`, "table"]);
  } else if (p.tableKey !== null) {
    const base = p.childKey !== null ? `${p.childKey}[${p.childIndex}].${p.tableKey}` : p.tableKey;
    tries.push([`${recId}|${base}`, "table"]);
  }
  if (p.childKey !== null && p.childIndex !== null) tries.push([`${recId}|${p.childKey}[${p.childIndex}]`, "row"]);
  if (p.childKey !== null) tries.push([`${recId}|${p.childKey}`, "table"]);
  for (const [k, kind] of tries) {
    const ids = (kind === "row" ? EV.rowCites : EV.tableCites).get(k);
    if (ids) return { kind, ids };
  }
  const ent = EV.entryCites.get(recId);
  return ent ? { kind: "entry", ids: ent } : null;
}

// ── confidence helpers ────────────────────────────────────────────────────────
export function levelRank(vm, level) {
  const i = vm.levels.indexOf(level);
  return i < 0 ? vm.levels.length : i;            // unknown → worse than the worst
}
export function worstLevel(vm, conf) {
  let worst = null;
  for (const v of Object.values(conf || {})) {
    const lv = v && v.level;
    if (lv == null) continue;
    if (worst === null || levelRank(vm, lv) > levelRank(vm, worst)) worst = lv;
  }
  return worst;
}
export function isLow(vm, level) { return level != null && levelRank(vm, level) >= vm.levels.length - 1; }

// Coder's triage number for one entry: low ratings + declared-value fields with no citation
// + server-side issues under this entry's path.
export function entryNeeds(vm, EV, rec, issues) {
  let n = 0;
  for (const v of Object.values(rec.confidence || {})) if (v && isLow(vm, v.level)) n += 1;
  for (const ch of Object.values(rec.child_confidence || {}))
    for (const v of Object.values(ch || {})) if (v && isLow(vm, v.level)) n += 1;
  const fv = rec.field_values || {};
  for (const f of vm.entries.fields) if (f.evidence === "value" && fv[f.name] != null && fv[f.name] !== "" && !EV.exact.has(`${rec.id}|${f.name}`)) n += 1;
  for (const ch of vm.entries.children) (fv[ch.key] || []).forEach((row, k) => {
    for (const f of ch.fields || []) if (f.evidence === "value" && row && row[f.name] != null && row[f.name] !== ""
        && !EV.exact.has(`${rec.id}|${ch.key}[${k}].${f.name}`)) n += 1;
  });
  // rows declared `evidence: row` (a condition, a table row) without their identifying item
  const tableRows = (node, fields, at) => {
    for (const f of fields || []) if (f.type === "table" && f.evidence === "row" && Array.isArray(node && node[f.name]))
      node[f.name].forEach((_, r) => { if (!EV.rowCites.has(`${rec.id}|${at}${f.name}[${r}]`)) n += 1; });
  };
  tableRows(fv, vm.entries.fields, "");
  for (const ch of vm.entries.children) (fv[ch.key] || []).forEach((row, k) => {
    if ((ch.evidence || "row") === "row" && !EV.rowCites.has(`${rec.id}|${ch.key}[${k}]`)) n += 1;
    tableRows(row, ch.fields, `${ch.key}[${k}].`);
  });
  const prefix = `${vm.entries.key}[${rec.entry_index}]`;
  for (const is of issues || []) if (typeof is.path === "string" && is.path.startsWith(prefix) && is.code !== "uncited_value" && is.code !== "uncited_row") n += 1;
  return n;
}

// ── misc ──────────────────────────────────────────────────────────────────────
export function undeclared(vm, scope, obj) {
  const declared = new Set(scope === "paper"
    ? [...vm.paper.fields.map((f) => f.name), "title", "doi", "year", "authors", "journal"]
    : [...vm.entries.fields.map((f) => f.name), ...vm.entries.children.map((c) => c.key)]);
  return Object.keys(obj || {}).filter((k) => !declared.has(k) && !RESERVED.has(k));
}
export function seedEntry(vm) {
  const blank = (f) => f.type === "table" ? [] : f.type === "multi" || f.type === "list" ? [] : f.type === "boolean" ? null : "";
  const out = {};
  for (const f of vm.entries.fields) out[f.name] = blank(f);
  for (const ch of vm.entries.children) out[ch.key] = [];
  return out;
}
export function orderedEntries(vm, EV, recs, mode, issues) {
  const withIdx = recs.map((rec, i) => ({ rec, i }));
  const rank = (r) => ({ unverified: 0, corrected: 0, flagged: 1, verified: 2 }[r.verification_status] ?? 0);
  if (mode === "low_conf") {
    withIdx.sort((a, b) => (entryNeeds(vm, EV, b.rec, issues) - entryNeeds(vm, EV, a.rec, issues)) || (rank(a.rec) - rank(b.rec)) || (a.i - b.i));
  } else if (mode === "unverified") {
    withIdx.sort((a, b) => (rank(a.rec) - rank(b.rec)) || (a.i - b.i));
  } else if (mode === "flagged") {
    withIdx.sort((a, b) => ((b.rec.verification_status === "flagged") - (a.rec.verification_status === "flagged")) || (a.i - b.i));
  }
  return withIdx;
}
// ── the spreadsheet shape ─────────────────────────────────────────────────────
// display.grid_rows names the unit of one grid / CSV row: "entries" (one per entry),
// "<child>" (one per sub-entry, parent scalars repeated) or "<child>.<table>" (one per
// table row inside a sub-entry — the flat table a meta-analyst keeps). Names are unique
// across the preset, so columns need no prefixes.
export function gridUnit(vm) {
  const gr = vm.gridRows || "entries";
  if (gr === "entries") return { child: null, table: null };
  const [ck, tk] = String(gr).split(".");
  const child = vm.entries.children.find((c) => c.key === ck) || null;
  const table = child && tk ? (child.fields || []).find((f) => f.name === tk && f.type === "table") || null : null;
  return { child, table };
}
const scalarFields = (fields) => (fields || []).filter((f) => f.type !== "table");
const col = (f, group, scope, summary = false) =>
  ({ key: f.name, label: f.label || formatKey(f.name), group, scope, summary });
export function gridColumns(vm) {
  const { child, table } = gridUnit(vm);
  const egroup = vm.entries.label || "Entry";
  const cols = scalarFields(vm.entries.fields).map((f) => col(f, egroup, "entry"));
  for (const f of vm.entries.fields.filter((f) => f.type === "table")) cols.push(col(f, egroup, "entry", true));
  for (const ch of vm.entries.children) {
    if (ch !== child) { cols.push({ key: ch.key, label: ch.label || formatKey(ch.key), group: egroup, scope: "entry", summary: true }); continue; }
    const cgroup = ch.label || formatKey(ch.key);
    cols.push(...scalarFields(ch.fields).map((f) => col(f, cgroup, "child")));
    for (const f of (ch.fields || []).filter((f) => f.type === "table")) {
      if (f === table) cols.push(...(f.columns || []).map((c) => col(c, f.label || formatKey(f.name), "row")));
      else cols.push(col(f, cgroup, "child", true));
    }
  }
  return cols;
}
export function gridRows(vm, recs) {
  const { child, table } = gridUnit(vm);
  const out = [];
  const pick = (fields, obj, into) => { for (const f of fields || []) into[f.name] = (obj || {})[f.name]; return into; };
  for (const rec of recs) {
    const fv = rec.field_values || {};
    const ei = rec.entry_index != null ? String(rec.entry_index) : "";
    const base = pick(vm.entries.fields, fv, {});
    for (const ch of vm.entries.children) if (ch !== child) base[ch.key] = fv[ch.key];
    const econf = rec.confidence || {};
    if (!child) { out.push({ rec, values: base, path: "", conf: econf, idx: ei }); continue; }
    const kids = Array.isArray(fv[child.key]) ? fv[child.key] : [];
    if (!kids.length) { out.push({ rec, values: base, path: "", conf: econf, idx: ei }); continue; }
    kids.forEach((kid, j) => {
      const cpath = `${child.key}[${j}]`;
      const conf = { ...econf, ...((rec.child_confidence || {})[cpath] || {}) };
      const cv = pick(child.fields, kid, { ...base });
      if (!table) { out.push({ rec, values: cv, path: cpath, conf, idx: `${ei}.${j}` }); return; }
      const trs = Array.isArray((kid || {})[table.name]) ? kid[table.name] : [];
      if (!trs.length) { out.push({ rec, values: cv, path: cpath, conf, idx: `${ei}.${j}` }); return; }
      trs.forEach((tr, r) => out.push({ rec, values: pick(table.columns, tr, { ...cv }), path: `${cpath}.${table.name}[${r}]`, conf, idx: `${ei}.${j}.${r}` }));
    });
  }
  return out;
}

export { esc };
