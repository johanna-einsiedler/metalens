// PaperLens record-rendering grammar (shared by workspace + catalog detail).
// renderValue(field_values) -> the rv-* grid; _table markers become tables;
// nested objects/arrays recurse. Optionally emits editable cells (data-path)
// whose corrections route through POST /api/records/{id}/verify.
// Vanilla ES module, no build step.

// `_rid` is the permanent id of a sub-entry / table row (evidence refers to rows by it):
// internal, kept on every edit, never rendered.
const SKIP = new Set(["evidence", "extraction_confidence", "_rid"]);
export function stripRowIds(node) {
  if (Array.isArray(node)) return node.map(stripRowIds);
  if (node && typeof node === "object") return Object.fromEntries(Object.entries(node).filter(([k]) => k !== "_rid").map(([k, v]) => [k, stripRowIds(v)]));
  return node;
}

export function renderValue(data, opts = {}) {
  return `<div class="rv-root">${renderNode(data, "", opts)}</div>`;
}

function renderNode(v, path, opts) {
  // A preset can declare a typed edit control for a field (dropdown / multi-select). Must
  // run BEFORE the array branch so a multi-select value (an array) renders as checkboxes.
  const ft = opts.editable && opts.fieldTypes && opts.fieldTypes[path];
  if (ft && (ft.type === "select" || ft.type === "multiselect") && (ft.options || []).length) {
    return renderControl(v, path, ft);
  }
  // preset render_hints (keyed by field name): an array field can render as CARDS — a
  // header line + a designated sub-table + everything else below — instead of a wide
  // nested table. Absent → the default shape-driven rendering below.
  const leaf = path.replace(/\[\d+\]$/, "").split(".").pop();
  const rh = opts.renderHints && leaf && opts.renderHints[leaf];
  if (rh && rh.as === "cards" && Array.isArray(v) && v.length) return renderCards(v, path, opts, rh);
  // No explicit hint, but the array is a "table of rows that each hold a sub-table" (the
  // cramped nested-table shape) → render as cards automatically. This gives custom prompts
  // (which can't declare render_hints) the readable layout; simple tables stay tables.
  if (!rh && Array.isArray(v) && v.length && isCardShape(v)) return renderCards(v, path, opts, autoCardHint(v));
  if (v === null || v === undefined) {
    // an empty value the coder can fill in: same cell as an extracted one, just blank
    if (opts.editable && path) return `<span contenteditable="plaintext-only" class="rv-editable rv-empty" data-path="${esc(path)}"></span>`;
    return `<span class="rv-null">—</span>`;
  }
  if (Array.isArray(v)) return renderArray(v, path, opts);
  if (typeof v === "object") {
    if (Array.isArray(v._table)) return renderTable(v._table, `${path}._table`, opts);
    return renderObj(v, path, opts);
  }
  const num = typeof v === "number";
  if (opts.editable) {
    return `<span contenteditable="plaintext-only" class="rv-editable${num ? " rv-num" : ""}"`
      + ` data-path="${esc(path)}">${esc(String(v))}</span>`;
  }
  // read-only cell still carries data-path so it can be linked to its source evidence
  return `<span class="rv-cell${num ? " rv-num" : ""}" data-path="${esc(path)}">${esc(String(v))}</span>`;
}

// A typed edit control declared by the preset's field_types. Produces markup only;
// workspace.js wires the change events (by class) through the same correction path as
// text cells. `select` → dropdown; `multiselect` → checkbox group (value = array);
// `allow_other` adds an "Other…" choice that reveals a free-text input.
function renderControl(v, path, ft) {
  const options = ft.options || [];
  if (ft.type === "multiselect") {
    const cur = Array.isArray(v) ? v.map(String) : (v == null || v === "" ? [] : [String(v)]);
    const boxes = options.map((o) =>
      `<label class="rv-chk"><input type="checkbox" value="${esc(o)}"${cur.includes(String(o)) ? " checked" : ""}/>${esc(o)}</label>`
    ).join("");
    return `<span class="rv-multi" data-path="${esc(path)}">${boxes}</span>`;
  }
  const val = (v == null) ? "" : String(v);
  const known = options.map(String);
  const isOther = !!ft.allow_other && val !== "" && !known.includes(val);
  const optionsHtml = [`<option value="">—</option>`]
    .concat(options.map((o) => `<option value="${esc(o)}"${String(o) === val ? " selected" : ""}>${esc(o)}</option>`))
    .concat(ft.allow_other ? [`<option value="__other__"${isOther ? " selected" : ""}>Other…</option>`] : [])
    .join("");
  const other = ft.allow_other
    ? `<input class="rv-other" type="text" placeholder="other value…"${isOther ? "" : " hidden"} value="${isOther ? esc(val) : ""}"/>`
    : "";
  return `<span class="rv-selwrap" data-path="${esc(path)}"><select class="rv-select">${optionsHtml}</select>${other}</span>`;
}

function renderObj(obj, path, opts) {
  const rows = Object.entries(obj)
    .filter(([k]) => !SKIP.has(k))
    .map(([k, val]) => {
      const child = path ? `${path}.${k}` : k;
      const nested = val && typeof val === "object";
      // The key carries the same data-path as its value, so clicking the FIELD NAME jumps
      // to the field's evidence too (wired in linkValueCells, only when evidence exists).
      // nested values span the full width (rv-row-block); scalars pack into columns
      return `<div class="rv-row${nested ? " rv-row-block" : ""}">`
        + `<div class="rv-key" data-path="${esc(child)}">${esc(formatKey(k))}</div>`
        + `<div class="rv-val${nested ? " rv-nested" : ""}">${renderNode(val, child, opts)}</div></div>`;
    }).join("");
  return `<div class="rv-obj">${rows}</div>`;
}

function renderArray(arr, path, opts) {
  if (arr.every((x) => x === null || typeof x !== "object")) {
    return arr.map((x, i) => renderNode(x, `${path}[${i}]`, opts)).join(", ") || `<span class="rv-null">[]</span>`;
  }
  return renderTable(arr, path, opts);
}

function renderTable(rows, path, opts) {
  if (!rows.length) return `<span class="rv-null">empty</span>`;
  const cols = [...new Set(rows.flatMap((r) =>
    r && typeof r === "object" ? Object.keys(r).filter((k) => !SKIP.has(k)) : []))];
  if (!cols.length) return renderArray(rows, path, opts);
  const head = cols.map((c) => `<th>${esc(formatKey(c))}</th>`).join("");
  const body = rows.map((r, i) =>
    `<tr>${cols.map((c) => `<td>${renderNode(r ? r[c] : null, `${path}[${i}].${c}`, opts)}</td>`).join("")}</tr>`
  ).join("");
  // wrap so a wide/nested table scrolls horizontally inside the panel instead of crushing
  // its columns into letter-by-letter wrapping
  return `<div class="rv-tablewrap"><table class="rv-table"><thead><tr>${head}</tr></thead>`
    + `<tbody>${body}</tbody></table></div>`;
}

// Is this an array of objects where at least one element contains a NESTED array-of-objects
// (a sub-table)? That's the table-in-table shape that renders unreadably as a wide table.
const _isObj = (x) => x && typeof x === "object" && !Array.isArray(x);
function isCardShape(arr) {
  if (!arr.every(_isObj)) return false;
  return arr.some((el) => Object.values(el).some((x) => Array.isArray(x) && x.length && _isObj(x[0])));
}
// Derive a card hint from the data: the first nested array-of-objects field is the table;
// scalar fields become the header; everything else falls below.
function autoCardHint(arr) {
  const el = arr.find(_isObj) || {};
  let table = null; const header = [];
  for (const [k, x] of Object.entries(el)) {
    if (SKIP.has(k)) continue;
    if (!table && Array.isArray(x) && x.length && _isObj(x[0])) { table = k; continue; }
    if (x === null || typeof x !== "object") header.push(k);
  }
  return { as: "cards", header, table };
}

// render_hints "cards": each element of the array becomes a card — a compact header line
// (hint.header keys), the hint.table field rendered as a full-width table, and every other
// field below as a labelled grid. Child paths are preserved so evidence-linking + editing
// keep working exactly as in the default layout.
function renderCards(arr, path, opts, hint) {
  const headerKeys = hint.header || [];
  const tableKey = hint.table;
  return arr.map((el, i) => {
    const base = `${path}[${i}]`;
    if (!el || typeof el !== "object" || Array.isArray(el)) return renderNode(el, base, opts);
    const head = headerKeys
      .filter((k) => el[k] !== null && el[k] !== undefined && el[k] !== "" && typeof el[k] !== "object")
      .map((k) => `<span class="rc-h"><span class="rc-hk">${esc(formatKey(k))}</span> ${esc(String(el[k]))}</span>`)
      .join("");
    const tbl = (tableKey && Array.isArray(el[tableKey]) && el[tableKey].length)
      ? renderNode(el[tableKey], `${base}.${tableKey}`, opts) : "";
    const rest = {};
    Object.keys(el).forEach((k) => {
      if (k === tableKey || headerKeys.includes(k) || SKIP.has(k)) return;
      rest[k] = el[k];
    });
    const below = Object.keys(rest).length ? `<div class="rc-below">${renderObj(rest, base, opts)}</div>` : "";
    return `<div class="rv-card">${head ? `<div class="rc-head">${head}</div>` : ""}${tbl}${below}</div>`;
  }).join("");
}

// ── spec-driven rendering (declarative presets) ──────────────────────────────
// A declared field renders by its TYPE, not by the shape of the value the model happened
// to return: an enum is a dropdown even when the value is a stray string, a number gets
// tabular numerals even when it arrived as text, a missing declared field is visibly
// missing. Undeclared keys still render by shape (renderNode) so nothing is hidden.

const _opts = (f) => (Array.isArray(f.options) ? f.options : []).map((o) =>
  (o && typeof o === "object") ? { value: o.value, label: o.label != null ? String(o.label) : String(o.value) }
                               : { value: o, label: String(o) });

function renderTypedValue(f, v, path, opts) {
  const t = f.type || "string";
  const editable = !!opts.editable;
  if (t === "table") return renderTypedTable(f, v, path, opts);
  if (v === undefined) return `<span class="rv-missing" title="declared by the preset, not returned by the model">—</span>`;
  if (t === "enum" && editable && (Array.isArray(f.options) ? f.options.length : false)) {
    const options = _opts(f);
    const val = v == null ? "" : String(v);
    const known = options.map((o) => String(o.value));
    const isOther = !!f.allow_other && val !== "" && !known.includes(val);
    const optionsHtml = [`<option value="">—</option>`]
      .concat(options.map((o) => `<option value="${esc(o.value)}"${String(o.value) === val ? " selected" : ""}>${esc(o.label)}</option>`))
      .concat(f.allow_other ? [`<option value="__other__"${isOther ? " selected" : ""}>Other…</option>`] : []).join("");
    const other = f.allow_other
      ? `<input class="rv-other" type="text" placeholder="other value…"${isOther ? "" : " hidden"} value="${isOther ? esc(val) : ""}"/>` : "";
    return `<span class="rv-selwrap" data-path="${esc(path)}"><select class="rv-select">${optionsHtml}</select>${other}</span>`;
  }
  if (t === "enum" && !editable) {
    const hit = _opts(f).find((o) => String(o.value) === String(v));
    return `<span class="rv-cell" data-path="${esc(path)}">${v == null ? '<span class="rv-null">—</span>' : esc(hit ? hit.label : String(v))}</span>`;
  }
  if (t === "multi" && editable && (Array.isArray(f.options) ? f.options.length : false)) {
    const cur = Array.isArray(v) ? v.map(String) : (v == null || v === "" ? [] : [String(v)]);
    const boxes = _opts(f).map((o) =>
      `<label class="rv-chk"><input type="checkbox" value="${esc(o.value)}"${cur.includes(String(o.value)) ? " checked" : ""}/>${esc(o.label)}</label>`).join("");
    return `<span class="rv-multi" data-path="${esc(path)}">${boxes}</span>`;
  }
  if (t === "boolean" && editable) {
    const val = v === true ? "true" : v === false ? "false" : "";
    return `<span class="rv-selwrap rv-bool" data-path="${esc(path)}"><select class="rv-select">`
      + `<option value=""${val === "" ? " selected" : ""}>—</option><option value="true"${val === "true" ? " selected" : ""}>true</option>`
      + `<option value="false"${val === "false" ? " selected" : ""}>false</option></select></span>`;
  }
  if (v === null) {
    const num = t === "integer" || t === "number";
    if (editable && t !== "list" && t !== "multi") {   // a blank the coder can type into (the model left it null)
      return `<span contenteditable="plaintext-only" class="rv-editable rv-empty${num ? " rv-num" : ""}${t === "text" ? " rv-text" : ""}"`
        + ` data-path="${esc(path)}"${f.help ? ` title="${esc(f.help)}"` : ""}></span>`;
    }
    return `<span class="rv-null" data-path="${esc(path)}">—</span>`;
  }
  if (t === "list" || (t === "multi" && Array.isArray(v))) {
    const items = Array.isArray(v) ? v : [v];
    if (!items.length) return `<span class="rv-null">[]</span>`;
    return `<span class="rv-cell rv-list" data-path="${esc(path)}">${items.map((x) => `<span class="rv-chip">${esc(String(x))}</span>`).join(" ")}</span>`;
  }
  if (typeof v === "object") return renderNode(v, path, opts);       // shape mismatch: show it anyway
  const num = t === "integer" || t === "number" || typeof v === "number";
  if (editable) {
    return `<span contenteditable="plaintext-only" class="rv-editable${num ? " rv-num" : ""}${t === "text" ? " rv-text" : ""}"`
      + ` data-path="${esc(path)}"${f.help ? ` title="${esc(f.help)}"` : ""}>${esc(String(v))}</span>`;
  }
  return `<span class="rv-cell${num ? " rv-num" : ""}${t === "text" ? " rv-text" : ""}" data-path="${esc(path)}">${esc(String(v))}</span>`;
}

// A table-typed field: typed columns in declared order, one row per element. A legacy
// dotted-key object (factor_loadings: {"F1.2": …}) is shown read-only as key/value rows —
// editing those keys is exactly what used to corrupt the record.
function renderTypedTable(f, v, path, opts) {
  const cols = f.columns || [];
  if (v && typeof v === "object" && !Array.isArray(v) && !Array.isArray(v._table)) {
    const rows = Object.entries(v).map(([k, x]) =>
      `<tr><td class="rv-cell">${esc(k)}</td><td>${renderNode(x, `${path}.${k}`, { ...opts, editable: false })}</td></tr>`).join("");
    return `<div class="rv-tablewrap"><table class="rv-table rv-kv"><thead><tr><th>key</th><th>value</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  const rows = Array.isArray(v) ? v : (v && Array.isArray(v._table) ? v._table : null);
  if (rows === null || rows === undefined) return `<span class="rv-missing">—</span>`;
  if (!rows.length) return `<span class="rv-null">empty</span>`;
  const names = cols.map((c) => c.name);
  const extra = [...new Set(rows.flatMap((r) => (r && typeof r === "object") ? Object.keys(r).filter((k) => !names.includes(k) && !SKIP.has(k)) : []))];
  const head = cols.map((c) => `<th title="${esc(c.help || "")}">${esc(c.label || formatKey(c.name))}</th>`).join("")
    + extra.map((k) => `<th>${esc(formatKey(k))}</th>`).join("");
  const body = rows.map((r, i) => {
    const base = `${path}[${i}]`;
    const cells = cols.map((c) => `<td>${renderTypedValue(c, r ? r[c.name] : undefined, `${base}.${c.name}`, opts)}</td>`).join("")
      + extra.map((k) => `<td>${renderNode(r ? r[k] : null, `${base}.${k}`, opts)}</td>`).join("");
    return `<tr data-row="${i}"><td class="rv-rowcite" data-rowpath="${esc(base)}"></td>${cells}</tr>`;
  }).join("");
  return `<div class="rv-tablewrap"><table class="rv-table rv-typed"><thead><tr><th class="rv-rowcite"></th>${head}</tr></thead>`
    + `<tbody>${body}</tbody></table></div>`;
}

// Declared fields of one object level, in declaration order. `defs` is the field list,
// `obj` the values, `basePath` "" for an entry, "records[2]" for a sub-entry row.
export function renderFields(defs, obj, basePath, opts) {
  const rows = defs.map((f) => {
    const path = basePath ? `${basePath}.${f.name}` : f.name;
    const v = obj ? obj[f.name] : undefined;
    const block = f.type === "table" || (v && typeof v === "object" && !Array.isArray(v) && f.type !== "multi" && f.type !== "list");
    const conf = f.confidence ? ` data-group="${esc(f.confidence)}"` : "";
    return `<div class="rv-row${block ? " rv-row-block" : ""} rv-decl${v === undefined ? " rv-row-missing" : ""}"${conf} data-field="${esc(f.name)}">`
      + `<div class="rv-key" data-path="${esc(path)}"${f.help ? ` title="${esc(f.help)}"` : ""}>${esc(f.label || formatKey(f.name))}</div>`
      + `<div class="rv-val${block ? " rv-nested" : ""}">${renderTypedValue(f, v, path, opts)}</div></div>`;
  }).join("");
  return `<div class="rv-obj">${rows}</div>`;
}

// A sub-entry array: a typed table (one row per element, row-citation cell first) or one
// card per element with its title. Paths keep the child index so evidence + edits bind.
export function renderChild(child, rows, opts) {
  const key = child.key;
  const list = Array.isArray(rows) ? rows : (rows && Array.isArray(rows._table) ? rows._table : null);
  if (list === null || list === undefined) return `<span class="rv-missing" title="declared by the preset, not returned by the model">—</span>`;
  if (!list.length) return `<span class="rv-null">none</span>`;
  const defs = child.fields || [];
  if ((child.layout || "table") === "table") {
    const names = defs.map((f) => f.name);
    const extra = [...new Set(list.flatMap((r) => (r && typeof r === "object") ? Object.keys(r).filter((k) => !names.includes(k) && !SKIP.has(k)) : []))];
    const head = defs.map((f) => `<th title="${esc(f.help || "")}"${f.confidence ? ` data-group="${esc(f.confidence)}"` : ""}>${esc(f.label || formatKey(f.name))}</th>`).join("")
      + extra.map((k) => `<th>${esc(formatKey(k))}</th>`).join("");
    const body = list.map((r, k) => {
      const base = `${key}[${k}]`;
      const cells = defs.map((f) => `<td${f.confidence ? ` data-group="${esc(f.confidence)}"` : ""}>${renderTypedValue(f, r ? r[f.name] : undefined, `${base}.${f.name}`, opts)}</td>`).join("")
        + extra.map((x) => `<td>${renderNode(r ? r[x] : null, `${base}.${x}`, opts)}</td>`).join("");
      return `<tr class="rv-childrow" data-rowpath="${esc(base)}"><td class="rv-rowcite" data-rowpath="${esc(base)}"></td>${cells}</tr>`;
    }).join("");
    return `<div class="rv-tablewrap rv-child" data-child="${esc(key)}"><table class="rv-table rv-typed"><thead><tr><th class="rv-rowcite"></th>${head}</tr></thead>`
      + `<tbody>${body}</tbody></table></div>`;
  }
  return `<div class="rv-child rv-childcards" data-child="${esc(key)}">` + list.map((r, k) => {
    const base = `${key}[${k}]`;
    const title = (child.title || "").replace(/\{(#|[A-Za-z_][A-Za-z0-9_]*)\}/g, (_, n) =>
      n === "#" ? String(k + 1) : (r && r[n] != null && typeof r[n] !== "object" ? String(r[n]) : "")).trim() || `${child.label || key} ${k + 1}`;
    return `<div class="rv-card rv-childrow" data-rowpath="${esc(base)}"><div class="rc-head"><span class="rv-rowcite" data-rowpath="${esc(base)}"></span>`
      + `<span class="rc-h">${esc(title)}</span><span class="rc-conf" data-rowpath="${esc(base)}"></span></div>${renderFields(defs, r, base, opts)}</div>`;
  }).join("") + `</div>`;
}

// A confidence badge for one group: level + a click-to-open notes line. The notes are what
// tell the coder WHAT to check, so they're one click away, never tooltip-only.
export function renderConfBadge(gid, group, rating, levels) {
  if (!rating) return `<span class="conf-badge conf-none" data-gid="${esc(gid)}" title="not rated">${esc(group ? group.label : formatKey(gid))} · —</span>`;
  const lv = rating.level == null ? "" : String(rating.level);
  const cls = _levelClass(lv, levels);
  const notes = rating.notes ? String(rating.notes) : "";
  return `<button type="button" class="conf-badge ${cls}" data-gid="${esc(gid)}" aria-expanded="false"`
    + ` title="${esc(group && group.help ? group.help : "")}">${esc(group ? group.label : formatKey(gid))} · ${esc(lv || "?")}`
    + (notes ? ` <span class="conf-caret">▾</span>` : "") + `</button>`
    + (notes ? `<div class="conf-notes" hidden>${esc(notes)}</div>` : "");
}
export function renderConfDot(level, levels, title) {
  if (level == null) return "";
  return `<span class="conf-dot ${_levelClass(String(level), levels)}" title="${esc(title || String(level))}"></span>`;
}
function _levelClass(lv, levels) {
  const ls = levels || ["high", "medium", "low"];
  const i = ls.indexOf(lv);
  if (i < 0) return "conf-unknown";
  if (i === 0) return "conf-high";
  if (i === ls.length - 1) return "conf-low";
  return "conf-medium";
}

export function renderEvidenceList(evidence) {
  if (!evidence || !evidence.length) return "";
  return evidence.map((e) =>
    `<div class="ev-block"><p class="ev-snippet">${esc(e.snippet || "")}</p><div class="ev-tags">`
    + (e.page ? `<span class="ev-tag ev-page">p${e.page}</span>` : "")
    + (e.source ? `<span class="ev-tag ev-source">${esc(e.source)}</span>` : "")
    + (e.field_path ? `<span class="ev-tag">${esc(e.field_path)}</span>` : "")
    + `</div></div>`).join("");
}

export function formatKey(k) {
  return String(k).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
export function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ── minimal markdown renderer for the prompt preview ────────────────────────────
// The preset prompts are markdown (# TASK, ## EFFECT SIZES, bullets, fenced JSON), and
// dumping them into a <pre> made a long wall of monospace nobody reads. No dependency:
// the CDN isn't reachable from a published page and the rest of this front-end is
// build-free. Escapes FIRST, so nothing in a prompt can inject markup.
export function renderMarkdown(src) {
  const esc_ = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const inline = (t) => esc_(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  const out = [];
  const lines = String(src || "").split("\n");
  let list = null;                 // "ul" | "ol" while a list is open
  let para = [];                   // buffered plain lines
  let fence = null;                // buffered fenced-code lines

  const flushPara = () => {
    if (para.length) { out.push(`<p>${para.map(inline).join("<br/>")}</p>`); para = []; }
  };
  const flushList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const flush = () => { flushPara(); flushList(); };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, "");

    if (fence !== null) {                                  // inside ```…```
      if (/^\s*```/.test(line)) { out.push(`<pre class="md-code">${esc_(fence.join("\n"))}</pre>`); fence = null; }
      else fence.push(raw);
      continue;
    }
    if (/^\s*```/.test(line)) { flush(); fence = []; continue; }

    if (!line.trim()) { flush(); continue; }

    const h = line.match(/^(#{1,4})\s+(.*)$/);            // # heading
    if (h) { flush(); const n = h[1].length; out.push(`<h${n + 2} class="md-h md-h${n}">${inline(h[2])}</h${n + 2}>`); continue; }

    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);          // 1. item
    if (ol) {
      flushPara();
      if (list !== "ol") { flushList(); out.push('<ol class="md-list">'); list = "ol"; }
      out.push(`<li>${inline(ol[1])}</li>`); continue;
    }
    const ul = line.match(/^\s*[-*\u2013\u2022]\s+(.*)$/);  // -, *, – or • item
    if (ul) {
      flushPara();
      if (list !== "ul") { flushList(); out.push('<ul class="md-list">'); list = "ul"; }
      out.push(`<li>${inline(ul[1])}</li>`); continue;
    }

    flushList();
    para.push(line);
  }
  if (fence !== null) out.push(`<pre class="md-code">${esc_(fence.join("\n"))}</pre>`);
  flush();
  return out.join("");
}

