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
    const known = options.map(String);
    const boxes = options.map((o) =>
      `<label class="rv-chk"><input type="checkbox" value="${esc(o)}"${cur.includes(String(o)) ? " checked" : ""}/>${esc(o)}</label>`
    ).join("");
    return `<span class="rv-multi${ft.allow_other ? " rv-multi-open" : ""}" data-path="${esc(path)}">${boxes}${multiFree(cur, known, !!ft.allow_other)}</span>`;
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

// The tail of a multi control. A stored value outside the declared options — the paper's own
// term — is drawn as a checked chip the coder can untick, so it is never dropped unseen; with
// `allow_other` an input follows where a new term is typed (Enter adds it).
export function freeChip(x) {
  return `<label class="rv-chk rv-free" title="the paper's own term"><input type="checkbox" value="${esc(x)}" checked data-free="1"/>${esc(x)}</label>`;
}
function multiFree(cur, known, open) {
  const chips = cur.filter((x) => !known.includes(x)).map(freeChip).join("");
  const input = open ? `<input class="rv-other rv-multi-other" type="text" placeholder="other…" title="a term of the paper's own — Enter adds it to the list"/>` : "";
  return chips + input;
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

// an option: its code, its label, and its help (the category's definition, shown on hover)
const _opts = (f) => (Array.isArray(f.options) ? f.options : []).map((o) =>
  (o && typeof o === "object") ? { value: o.value, label: o.label != null ? String(o.label) : String(o.value), help: typeof o.help === "string" ? o.help : "" }
                               : { value: o, label: String(o), help: "" });
// the tip a select carries: the chosen option's help, else the field's own
export function selectTitle(sel) {
  const o = sel.selectedOptions && sel.selectedOptions[0];
  return (o && o.title) || sel.dataset.help || "";
}
// the tip of a field's ⓘ: its help and, for an enum or multi field, every option as "label — definition"
export function optionsTip(f) {
  const lines = _opts(f).map((o) => (o.help ? `${o.label} — ${o.help}` : o.label));
  return [f.help || "", ...lines].filter(Boolean).join("\n");
}
export function fieldTip(f) {
  return (Array.isArray(f.options) && f.options.length) ? optionsTip(f) : (f.help || "");
}
// a child row's title from the child's template ("{arm_id} · {arm_label}"), an enum shown as its label
export function rowTitleOf(child, r, k) {
  const byName = new Map((child.fields || []).map((f) => [f.name, f]));
  const t = (child.title || "").replace(/\{(#|[A-Za-z_][A-Za-z0-9_]*)\}/g, (_, n) => {
    if (n === "#") return String(k + 1);
    const v = r ? r[n] : null;
    if (v == null || typeof v === "object") return "";
    const f = byName.get(n), o = f && Array.isArray(f.options) ? _opts(f).find((x) => String(x.value) === String(v)) : null;
    return o ? o.label : String(v);
  }).replace(/\s+/g, " ").trim().replace(/^[\s–—:·-]+|[\s–—:·-]+$/g, "");
  return t || `${child.label || child.key} ${k + 1}`;
}

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
    const chosen = options.find((o) => String(o.value) === val);
    const optionsHtml = [`<option value="">—</option>`]
      .concat(options.map((o) => `<option value="${esc(o.value)}"${String(o.value) === val ? " selected" : ""}${o.help ? ` title="${esc(o.help)}"` : ""}>${esc(o.label)}</option>`))
      .concat(f.allow_other ? [`<option value="__other__"${isOther ? " selected" : ""}>Other…</option>`] : []).join("");
    const other = f.allow_other
      ? `<input class="rv-other" type="text" placeholder="other value…"${isOther ? "" : " hidden"} value="${isOther ? esc(val) : ""}"/>` : "";
    // the select's tip: the chosen option's definition, else the field's help (kept in data-help);
    // every option's definition is on the field's own ⓘ (fieldTip), beside the term or in the column header
    const title = (chosen && chosen.help) || f.help || "";
    return `<span class="rv-selwrap" data-path="${esc(path)}"><select class="rv-select"${f.help ? ` data-help="${esc(f.help)}"` : ""}${title ? ` title="${esc(title)}" data-tip="${esc(title)}"` : ""}>${optionsHtml}</select>${other}</span>`;
  }
  if (t === "enum" && !editable) {
    const hit = _opts(f).find((o) => String(o.value) === String(v));
    return `<span class="rv-cell" data-path="${esc(path)}">${v == null ? '<span class="rv-null">—</span>' : esc(hit ? hit.label : String(v))}</span>`;
  }
  if (t === "multi" && editable && (Array.isArray(f.options) ? f.options.length : false)) {
    const cur = Array.isArray(v) ? v.map(String) : (v == null || v === "" ? [] : [String(v)]);
    const options = _opts(f);
    const boxes = options.map((o) =>
      `<label class="rv-chk"${o.help ? ` title="${esc(o.help)}" data-tip="${esc(o.help)}"` : ""}><input type="checkbox" value="${esc(o.value)}"${cur.includes(String(o.value)) ? " checked" : ""}/>${esc(o.label)}</label>`).join("");
    return `<span class="rv-multi${f.allow_other ? " rv-multi-open" : ""}" data-path="${esc(path)}">${boxes}${multiFree(cur, options.map((o) => String(o.value)), !!f.allow_other)}</span>`;
  }
  if (t === "boolean" && editable) {
    const val = v === true ? "true" : v === false ? "false" : "";
    return `<span class="rv-selwrap rv-bool" data-path="${esc(path)}"><select class="rv-select">`
      + `<option value=""${val === "" ? " selected" : ""}>—</option><option value="true"${val === "true" ? " selected" : ""}>true</option>`
      + `<option value="false"${val === "false" ? " selected" : ""}>false</option></select></span>`;
  }
  if (t === "list" && editable) {                       // a list the coder types as comma-separated items
    const items = Array.isArray(v) ? v : v === null || v === undefined ? [] : [v];
    return `<span contenteditable="plaintext-only" class="rv-editable rv-listed${items.length ? "" : " rv-empty"}" data-path="${esc(path)}"`
      + ` title="${esc((f.help ? f.help + " — " : "") + "comma-separated")}">${esc(items.map((x) => String(x)).join(", "))}</span>`;
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
  // a row the coder adds by hand: every declared column, blank — the paper may report a
  // factor (or any other row) the model missed, and it needs somewhere to go
  const addBtn = opts.editable && cols.length
    ? `<button type="button" class="rv-addrow" data-path="${esc(path)}" title="add a row — logged as a correction"`
      + ` data-blank="${esc(JSON.stringify(blankRow(cols)))}">＋ row</button>`
    : "";
  if (v && typeof v === "object" && !Array.isArray(v) && !Array.isArray(v._table)) {
    const rows = Object.entries(v).map(([k, x]) =>
      `<tr><td class="rv-cell">${esc(k)}</td><td>${renderNode(x, `${path}.${k}`, { ...opts, editable: false })}</td></tr>`).join("");
    return `<div class="rv-tablewrap"><table class="rv-table rv-kv"><thead><tr><th>key</th><th>value</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  const rows = Array.isArray(v) ? v : (v && Array.isArray(v._table) ? v._table : null);
  if (rows === null || rows === undefined) return `<span class="rv-missing">—</span> ${addBtn}`;
  if (!rows.length) return `<span class="rv-null">empty</span> ${addBtn}`;
  const names = cols.map((c) => c.name);
  const extra = [...new Set(rows.flatMap((r) => (r && typeof r === "object") ? Object.keys(r).filter((k) => !names.includes(k) && !SKIP.has(k)) : []))];
  const head = cols.map((c) => { const t = fieldTip(c); return `<th title="${esc(t)}"${t ? ` data-tip="${esc(t)}"` : ""}>${esc(c.label || formatKey(c.name))}</th>`; }).join("")
    + extra.map((k) => `<th>${esc(formatKey(k))}</th>`).join("")
    + (opts.editable ? `<th class="rv-rowdel-h"></th>` : "");
  const body = rows.map((r, i) => {
    const base = `${path}[${i}]`;
    const cells = cols.map((c) => `<td>${renderTypedValue(c, r ? r[c.name] : undefined, `${base}.${c.name}`, opts)}</td>`).join("")
      + extra.map((k) => `<td>${renderNode(r ? r[k] : null, `${base}.${k}`, opts)}</td>`).join("")
      + (opts.editable ? `<td class="rv-rowdel-c"><button type="button" class="rv-rowdel" data-path="${esc(path)}" data-row="${i}" title="remove this row — logged as a correction">✕</button></td>` : "");
    return `<tr data-row="${i}"><td class="rv-rowcite" data-rowpath="${esc(base)}"></td>${cells}</tr>`;
  }).join("");
  return `<div class="rv-tablewrap"><table class="rv-table rv-typed"><thead><tr><th class="rv-rowcite"></th>${head}</tr></thead>`
    + `<tbody>${body}</tbody></table>${addBtn ? `<div class="rv-tableadd">${addBtn}</div>` : ""}</div>`;
}

// Declared fields of one object level, in declaration order. `defs` is the field list,
// `obj` the values, `basePath` "" for an entry, "records[2]" for a sub-entry row.
export function renderFields(defs, obj, basePath, opts) {
  const rows = defs.map((f) => {
    const path = basePath ? `${basePath}.${f.name}` : f.name;
    const v = obj ? obj[f.name] : undefined;
    const block = f.type === "table" || (v && typeof v === "object" && !Array.isArray(v) && f.type !== "multi" && f.type !== "list");
    const conf = f.confidence ? ` data-group="${esc(f.confidence)}"` : "";
    const tip = fieldTip(f);
    return `<div class="rv-row${block ? " rv-row-block" : ""} rv-decl${v === undefined ? " rv-row-missing" : ""}"${conf} data-field="${esc(f.name)}">`
      + `<div class="rv-key${tip ? " rv-key-help" : ""}" data-path="${esc(path)}"${tip ? ` title="${esc(tip)}" data-tip="${esc(tip)}"` : ""}>${esc(f.label || formatKey(f.name))}${tip ? `<span class="rv-help" title="${esc(tip)}" data-tip="${esc(tip)}" tabindex="0">ⓘ</span>` : ""}</div>`
      + `<div class="rv-val${block ? " rv-nested" : ""}">${renderTypedValue(f, v, path, opts)}</div></div>`;
  }).join("");
  return `<div class="rv-obj">${rows}</div>`;
}

// A blank row of declared fields, typed like a seeded entry: [] for tables, multi and lists,
// null for everything else — so every cell is immediately editable.
export function blankRow(defs) {
  return Object.fromEntries((defs || []).map((f) => [f.name, f.type === "table" || f.type === "multi" || f.type === "list" ? [] : null]));
}

// A sub-entry array: a typed table (one row per element, row-citation cell first) or one
// card per element with its title. Paths keep the child index so evidence + edits bind.
// Editable, it carries "＋ <label>" and a "✕" per row (wired like a typed table's rows, saved as a
// correction of the whole record). `opts.show` (a Set of field names) draws only those fields;
// `opts.only` (a Set of row indices) draws only those rows — paths keep the real index — and
// then neither button, since the rows are being shown under a piece of evidence, not managed.
export function renderChild(child, rows, opts) {
  const key = child.key;
  const label = child.label || formatKey(key);
  const manage = !!opts.editable && !opts.only;
  const addBtn = manage
    ? `<div class="rv-tableadd"><button type="button" class="rv-addrow" data-path="${esc(key)}" title="add ${esc(label.toLowerCase())} — logged as a correction"`
      + ` data-blank="${esc(JSON.stringify(blankRow(child.fields)))}">＋ ${esc(label)}</button></div>`
    : "";
  const del = (k) => manage ? `<button type="button" class="rv-rowdel" data-path="${esc(key)}" data-row="${k}" title="remove this ${esc(label.toLowerCase())} — logged as a correction">✕</button>` : "";
  const list = Array.isArray(rows) ? rows : (rows && Array.isArray(rows._table) ? rows._table : null);
  if (list === null || list === undefined) return `<span class="rv-missing" title="declared by the preset, not returned by the model">—</span>${addBtn}`;
  if (!list.length) return `<span class="rv-null">none</span>${addBtn}`;
  const defs = (child.fields || []).filter((f) => !opts.show || opts.show.has(f.name));
  const picked = list.map((r, k) => [r, k]).filter(([, k]) => !opts.only || opts.only.has(k));
  if ((child.layout || "table") === "table") {
    const names = (child.fields || []).map((f) => f.name);
    const extra = opts.show ? [] : [...new Set(list.flatMap((r) => (r && typeof r === "object") ? Object.keys(r).filter((k) => !names.includes(k) && !SKIP.has(k)) : []))];
    // opts.rowTitle: a leading cell naming the row ("C · control") when the fields its title is made of are not shown
    const head = (opts.rowTitle ? `<th class="rv-rowtitle"></th>` : "")
      + defs.map((f) => { const t = fieldTip(f); return `<th title="${esc(t)}"${t ? ` data-tip="${esc(t)}"` : ""}${f.confidence ? ` data-group="${esc(f.confidence)}"` : ""}>${esc(f.label || formatKey(f.name))}</th>`; }).join("")
      + extra.map((k) => `<th>${esc(formatKey(k))}</th>`).join("") + (manage ? `<th class="rv-rowdel-h"></th>` : "");
    const body = picked.map(([r, k]) => {
      const base = `${key}[${k}]`;
      const cells = (opts.rowTitle ? `<td class="rv-rowtitle">${esc(rowTitleOf(child, r, k))}</td>` : "")
        + defs.map((f) => `<td${f.confidence ? ` data-group="${esc(f.confidence)}"` : ""}>${renderTypedValue(f, r ? r[f.name] : undefined, `${base}.${f.name}`, opts)}</td>`).join("")
        + extra.map((x) => `<td>${renderNode(r ? r[x] : null, `${base}.${x}`, opts)}</td>`).join("")
        + (manage ? `<td class="rv-rowdel-c">${del(k)}</td>` : "");
      return `<tr class="rv-childrow" data-rowpath="${esc(base)}"><td class="rv-rowcite" data-rowpath="${esc(base)}"></td>${cells}</tr>`;
    }).join("");
    return `<div class="rv-tablewrap rv-child" data-child="${esc(key)}"><table class="rv-table rv-typed"><thead><tr><th class="rv-rowcite"></th>${head}</tr></thead>`
      + `<tbody>${body}</tbody></table></div>${addBtn}`;
  }
  return `<div class="rv-child rv-childcards" data-child="${esc(key)}">` + picked.map(([r, k]) => {
    const base = `${key}[${k}]`;
    const title = (child.title || "").replace(/\{(#|[A-Za-z_][A-Za-z0-9_]*)\}/g, (_, n) =>
      n === "#" ? String(k + 1) : (r && r[n] != null && typeof r[n] !== "object" ? String(r[n]) : "")).trim() || `${label} ${k + 1}`;
    return `<div class="rv-card rv-childrow" data-rowpath="${esc(base)}"><div class="rc-head"><span class="rv-rowcite" data-rowpath="${esc(base)}"></span>`
      + `<span class="rc-h">${esc(title)}</span><span class="rc-conf" data-rowpath="${esc(base)}"></span>${del(k)}</div>${renderFields(defs, r, base, opts)}</div>`;
  }).join("") + `</div>${addBtn}`;
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
  const esc_ = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  // A link target is kept only when it cannot run code or leave the reader somewhere odd:
  // https, a DOI, a same-page anchor, or a path on this site. Anything else stays literal text.
  const link = (text, href) => {
    const h = href.replace(/&amp;/g, "&");
    if (/^https?:\/\//i.test(h)) return `<a href="${esc_(h)}" target="_blank" rel="noopener">${text}</a>`;
    if (/^doi:/i.test(h)) return `<a href="https://doi.org/${esc_(h.slice(4))}" target="_blank" rel="noopener">${text}</a>`;
    if (/^#[A-Za-z0-9_-]+$/.test(h) || /^\/[A-Za-z0-9]/.test(h)) return `<a href="${esc_(h)}">${text}</a>`;
    return `[${text}](${esc_(h)})`;
  };
  // A picture: a file on this site (/static/…) or an https address; anything else stays text.
  const image = (alt, src) => {
    const s = src.replace(/&amp;/g, "&");
    if (/^\/static\/[A-Za-z0-9._\/-]+$/.test(s) || /^https:\/\//i.test(s)) return `<img class="md-img" src="${esc_(s)}" alt="${alt}" loading="lazy">`;
    return `![${alt}](${esc_(s)})`;
  };
  // One tokenising pass, so nothing runs inside a code span: code · ![alt](src) · [text](href) ·
  // **bold** · *em* (no * adjacent) · _em_ (non-word on both sides, so snake_case stays) · <br>.
  const inline = (t) => esc_(t).replace(
    /`([^`]+)`|!\[([^\]\n]*)\]\(([^)\s]+)\)|\[([^\]\n]+)\]\(([^)\s]+)\)|\*\*([^*\n]+)\*\*|(^|[^\w*])\*([^*\n]+?)\*(?![\w*])|(^|[^\w])_([^_\n]+?)_(?![\w])|&lt;br\s*\/?&gt;/g,
    (m, code, alt, src, ltxt, href, bold, pre1, em1, pre2, em2) => {
      if (code !== undefined) return `<code>${code}</code>`;
      if (alt !== undefined) return image(alt, src);
      if (ltxt !== undefined) return link(ltxt, href);
      if (bold !== undefined) return `<strong>${bold}</strong>`;
      if (em1 !== undefined) return `${pre1}<em>${em1}</em>`;
      if (em2 !== undefined) return `${pre2}<em>${em2}</em>`;
      return "<br/>";
    });
  const slug = (t) => t.replace(/<[^>]*>/g, "").replace(/&[a-z]+;/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

  const out = [];
  const lines = String(src || "").split("\n");
  let list = null;                 // "ul" | "ol" while a list is open
  let para = [];                   // buffered plain lines
  let fence = null;                // buffered fenced-code lines
  let fenceLen = 0;                // backticks that opened the fence (a longer fence may hold shorter ones)
  let fenceTitle = null;           // ```fold <title> → a closed <details> with that summary
  let fenceFigure = null;          // ```figure <name> → a placeholder the page fills (static/card_figures.js)
  const fenced = (lines_) => {
    if (fenceFigure) return `<div class="md-figure" data-figure="${fenceFigure}" data-alt="${esc_(lines_.join(" ").trim())}"></div>`;
    const pre = `<pre class="md-code">${esc_(lines_.join("\n"))}</pre>`;
    return fenceTitle ? `<details class="md-fold md-titled"><summary>${esc_(fenceTitle)}</summary>${pre}</details>` : pre;
  };
  let quote = null;                // buffered blockquote lines
  let table = null;                // {align, rows} while a pipe table is open

  const flushPara = () => {
    if (para.length) { out.push(`<p>${para.map(inline).join("<br/>")}</p>`); para = []; }
  };
  const flushList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const flushQuote = () => {
    if (quote) { out.push(`<blockquote class="md-quote">${renderMarkdown(quote.join("\n"))}</blockquote>`); quote = null; }
  };
  const flushTable = () => {
    if (!table) return;
    const { align, rows } = table;
    const cell = (tag, c, i) => `<${tag}${align[i] ? ` style="text-align:${align[i]}"` : ""}>${inline(c)}</${tag}>`;
    const row = (tag, r) => `<tr>${align.map((_, i) => cell(tag, r[i] == null ? "" : r[i], i)).join("")}</tr>`;
    out.push(`<div class="md-table-wrap"><table class="md-table"><thead>${row("th", rows[0])}</thead><tbody>${rows.slice(1).map((r) => row("td", r)).join("")}</tbody></table></div>`);
    table = null;
  };
  const flush = () => { flushPara(); flushList(); flushQuote(); flushTable(); };

  const splitRow = (line) => {
    const cells = line.trim().split(/(?<!\\)\|/).map((c) => c.replace(/\\\|/g, "|").trim());
    if (cells.length && cells[0] === "") cells.shift();
    if (cells.length && cells[cells.length - 1] === "") cells.pop();
    return cells;
  };
  // the delimiter row: cells of dashes with optional alignment colons, at least one pipe
  const isSep = (line) => /\|/.test(line || "") && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line || "");

  for (let k = 0; k < lines.length; k++) {
    const raw = lines[k];
    const line = raw.replace(/\s+$/, "");

    if (fence !== null) {                                  // inside ```…```
      const close = line.match(/^\s*(`{3,})\s*$/);
      if (close && close[1].length >= fenceLen) { out.push(fenced(fence)); fence = null; }
      else fence.push(raw);
      continue;
    }
    const open = line.match(/^\s*(`{3,})(.*)$/);
    if (open) {
      flush(); fence = []; fenceLen = open[1].length;
      const info = open[2].trim();
      fenceTitle = /^fold\s+\S/.test(info) ? info.replace(/^fold\s+/, "") : null;
      const fig = info.match(/^figure\s+([a-z0-9-]{1,40})$/);
      fenceFigure = fig ? fig[1] : null;
      continue;
    }

    if (table) {                                           // rows keep coming while they carry a pipe
      if (line.includes("|") && line.trim()) { table.rows.push(splitRow(line)); continue; }
      flushTable();
    }

    const bq = line.match(/^\s*>\s?(.*)$/);                 // > quote (a bare > keeps a blank line)
    if (bq) { flushPara(); flushList(); flushTable(); if (!quote) quote = []; quote.push(bq[1]); continue; }
    flushQuote();

    if (!line.trim()) { flush(); continue; }

    if (line.includes("|") && isSep(lines[k + 1])) {       // | a | b |  +  |---|---|
      flush();
      const align = splitRow(lines[k + 1]).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : ""));
      table = { align, rows: [splitRow(line)] };
      k += 1;
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flush(); out.push('<hr class="md-hr"/>'); continue; }

    const h = line.match(/^(#{1,4})\s+(.*)$/);            // # heading
    if (h) { flush(); const n = h[1].length; const body = inline(h[2]); out.push(`<h${n + 2} id="md-${slug(body)}" class="md-h md-h${n}">${body}</h${n + 2}>`); continue; }

    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);          // 1. item
    if (ol) {
      flushPara();
      if (list !== "ol") { flushList(); out.push('<ol class="md-list">'); list = "ol"; }
      out.push(`<li>${inline(ol[1])}</li>`); continue;
    }
    const ul = line.match(/^\s*[-*–•]\s+(.*)$/);  // -, *, – or • item
    if (ul) {
      flushPara();
      if (list !== "ul") { flushList(); out.push('<ul class="md-list">'); list = "ul"; }
      out.push(`<li>${inline(ul[1])}</li>`); continue;
    }

    flushList();
    para.push(/( {2,}|\\)$/.test(raw) ? line + "<br/>" : line);   // two trailing spaces or \ = a hard break
  }
  if (fence !== null) out.push(fenced(fence));
  flush();
  return out.join("");
}

