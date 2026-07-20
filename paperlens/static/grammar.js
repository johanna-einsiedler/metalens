// PaperLens record-rendering grammar (shared by workspace + catalog detail).
// renderValue(field_values) -> the rv-* grid; _table markers become tables;
// nested objects/arrays recurse. Optionally emits editable cells (data-path)
// whose corrections route through POST /api/records/{id}/verify.
// Vanilla ES module, no build step.

const SKIP = new Set(["evidence", "extraction_confidence"]);

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
  if (v === null || v === undefined) return `<span class="rv-null">—</span>`;
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
  return `<table class="rv-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

export function renderConfidence(conf) {
  if (!conf || typeof conf !== "object") return "";
  const badges = Object.entries(conf).map(([block, v]) => {
    const lvl = (v && v.level) || "medium";
    const notes = (v && v.notes) || "";
    return `<span class="confidence-badge confidence-${esc(lvl)}" title="${esc(notes)}">`
      + `${esc(formatKey(block))}: ${esc(lvl)}</span>`;
  }).join("");
  return badges ? `<div class="confidence-row">${badges}</div>` : "";
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
