// Cross-record spreadsheet grid: one row per record, one column per field, with
// sub_view group headers over the columns and per-cell evidence linking. Renders
// the wide (~60-column) shape that the per-record card stack can't show at a glance.
// The renderer is dumb: it lays out records × columns and emits cell/row events; the
// caller (workspace or dataset) owns highlight/navigation via the callbacks.
import { esc } from "/static/grammar.js";

const SKIP = new Set(["evidence", "extraction_confidence", "confidence"]);

// Column order = each sub_view's include_keys (dedup, groups kept contiguous),
// then any extra keys present on records but not claimed by a sub_view.
function columnsFor(records, subViews) {
  const cols = [], seen = new Set();
  for (const sv of subViews || []) {
    for (const k of sv.include_keys || []) {
      if (!seen.has(k)) { seen.add(k); cols.push({ key: k, group: sv.label || sv.id || "" }); }
    }
  }
  const extra = new Set();
  for (const r of records) {
    for (const k of Object.keys(r.field_values || {})) {
      if (!seen.has(k) && !SKIP.has(k)) extra.add(k);
    }
  }
  for (const k of extra) cols.push({ key: k, group: "Other" });
  return cols;
}

// Collapse the per-column groups into contiguous spans for the top header row.
function groupSpans(cols) {
  const spans = [];
  for (const c of cols) {
    const last = spans[spans.length - 1];
    if (last && last.label === c.group) last.span += 1;
    else spans.push({ label: c.group, span: 1 });
  }
  return spans;
}

function cellText(v) {
  if (v === null || v === undefined || v === "") return "";
  if (Array.isArray(v)) return v.map((x) => (x && typeof x === "object" ? JSON.stringify(x) : String(x))).join("; ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
// a nested table / sub-entry list that is not the row unit: show its size, not its JSON
function summaryText(v) { return Array.isArray(v) ? `${v.length} row${v.length === 1 ? "" : "s"}` : cellText(v); }

// Most-specific covering evidence for a column key. recEv entries are
// {i, page, path} with path already stripped of the record core prefix.
function covering(colKey, recEv) {
  let best = null;
  for (const x of recEv) {
    const hit = x.path === "" || x.path === colKey
      || colKey.startsWith(x.path + ".") || colKey.startsWith(x.path + "[");
    if (hit && (!best || x.path.length > best.path.length)) best = x;
  }
  return best;
}

/**
 * renderGrid(host, opts)
 *   Legacy shape — records + subViews: one row per record, columns from sub_views.
 *   Spec shape   — rows + columns (spec.js gridRows / gridColumns): one row per entry,
 *                  sub-entry or table row, as the preset's display.grid_rows declares.
 *   records     [{id, entry_index, field_values, verification_status}]
 *   subViews    [{label, include_keys}]
 *   columns     [{key, label, group, scope, summary}]
 *   rows        [{rec, values, path, conf, idx}]
 *   evidenceFor (record) => [{i, page, path}]   // path pre-stripped; default () => []
 *   onCellClick (record, colKey, coveringOrNull, event) => void
 *   onRowClick  (record) => void                // optional (e.g. open in workspace)
 *   confidenceFor (unit) => level|null          // unit = the row (spec shape) or the record
 *   levels      [best … worst]                  // the preset's confidence levels (for the dot colour)
 *   maxRows     number                          // cap; default 500
 */
export function renderGrid(host, opts) {
  const { records = [], subViews = [], columns = null, rows = null, evidenceFor = () => [],
          onCellClick, onRowClick, onCellEdit = null, confidenceFor = null, levels = ["high", "medium", "low"],
          maxRows = 500 } = opts || {};
  const specMode = Array.isArray(rows) && Array.isArray(columns);
  const units = specMode ? rows : records.map((r) => ({
    rec: r, values: r.field_values || {}, path: "", conf: r.confidence || {},
    idx: r.entry_index != null ? String(r.entry_index) : "" }));
  if (!units.length) { host.innerHTML = '<p class="muted">No records to show.</p>'; return; }
  const cols = specMode ? columns : columnsFor(records, subViews);
  const shown = units.slice(0, maxRows);
  const spans = groupSpans(cols);

  const grpRow = `<tr class="grid-grp"><th class="grid-idx"></th>`
    + spans.map((g) => `<th colspan="${g.span}">${esc(g.label)}</th>`).join("") + `</tr>`;
  const colRow = `<tr class="grid-col"><th class="grid-idx">#</th>`
    + cols.map((c) => `<th title="${esc(c.key)}">${esc(c.label || c.key)}</th>`).join("") + `</tr>`;

  const body = shown.map((u, ui) => {
    const r = u.rec;
    const recEv = evidenceFor(r) || [];
    const st = r.verification_status || "";
    const tds = cols.map((c) => {
      // a sub-entry / table-row cell is addressed by its record-relative path, so the
      // covering evidence is that row's own item, then the sub-entry's, then the entry's
      const cellPath = specMode && c.scope !== "entry" && u.path ? `${u.path}.${c.key}` : c.key;
      const cov = covering(cellPath, recEv);
      const linked = cov ? " grid-linked" : "";
      const val = c.summary ? summaryText(u.values[c.key]) : cellText(u.values[c.key]);
      // a scalar cell edits in place (like a card value); a nested summary stays read-only
      const editable = onCellEdit && specMode && !c.summary;
      return `<td class="grid-cell${linked}${editable ? " grid-edit" : ""}" data-col="${esc(c.key)}" data-path="${esc(cellPath)}"`
        + (cov ? ` data-eid="${cov.i}" data-page="${cov.page}"` : "")
        + (editable ? ` contenteditable="plaintext-only" spellcheck="false"` : "")
        + `>${esc(val)}</td>`;
    }).join("");
    const lv = confidenceFor ? confidenceFor(specMode ? u : r) : null;
    const li = lv == null ? -1 : levels.indexOf(String(lv));
    const lcls = li < 0 ? "conf-unknown" : li === 0 ? "conf-high" : li === levels.length - 1 ? "conf-low" : "conf-medium";
    return `<tr data-ri="${ui}" data-rid="${esc(r.id || "")}"><td class="grid-idx" title="${esc(st)}${lv != null ? ` · confidence ${esc(String(lv))}` : ""}">`
      + `${esc(u.idx)}${st ? `<span class="grid-st ${esc(st)}"></span>` : ""}`
      + (lv != null ? `<span class="conf-dot ${lcls}"></span>` : "") + `</td>${tds}</tr>`;
  }).join("");

  const note = units.length > shown.length
    ? `<p class="muted grid-note">Showing ${shown.length} of ${units.length} rows.</p>` : "";

  host.innerHTML = `<div class="grid-wrap"><table class="rec-grid">`
    + `<thead>${grpRow}${colRow}</thead><tbody>${body}</tbody></table></div>${note}`;

  // wire cells + rows
  host.querySelectorAll("tr[data-ri]").forEach((tr) => {
    const unit = shown[+tr.dataset.ri];
    if (!unit) return;
    const rec = unit.rec;
    if (onRowClick) {
      const idxCell = tr.querySelector(".grid-idx");
      if (idxCell) { idxCell.classList.add("grid-clickable"); idxCell.onclick = () => onRowClick(rec); }
    }
    tr.querySelectorAll(".grid-cell").forEach((td) => {
      const col = td.dataset.col;
      const cov = td.dataset.eid != null
        ? { i: +td.dataset.eid, page: +td.dataset.page } : null;
      if (onCellClick) td.onclick = (e) => onCellClick(rec, col, cov, e);
      else if (onRowClick && cov == null) td.onclick = () => onRowClick(rec);
      if (td.classList.contains("grid-edit")) wireCellEdit(td, unit, cols.find((c) => c.key === col), onCellEdit);
    });
  });
}

// In-place editing of one grid cell. Enter commits, Escape reverts, blur commits when the
// text changed. The value is typed after the stored one: a number stays a number, a
// boolean a boolean, a list of scalars is split on commas, an emptied cell becomes null.
// The edit path is exact: an entry field, the sub-entry's own field, or the table row's.
function wireCellEdit(td, unit, col, onCellEdit) {
  if (!col) return;
  const seg = String(unit.path || "").split(".");
  const path = col.scope === "entry" ? col.key
    : col.scope === "child" ? `${seg[0]}.${col.key}`
    : `${unit.path}.${col.key}`;
  td.dataset.editPath = path;
  td.dataset.orig = td.textContent;
  td.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); td.blur(); }
    else if (e.key === "Escape") { td.textContent = td.dataset.orig; td.blur(); }
  });
  td.addEventListener("blur", () => {
    const now = td.textContent;
    if (now === td.dataset.orig) return;
    const was = unit.values[col.key];
    let val;
    const t = now.trim();
    if (t === "") val = null;
    else if (Array.isArray(was)) val = t.split(",").map((x) => x.trim()).filter(Boolean);
    else if (typeof was === "boolean" && /^(true|false)$/i.test(t)) val = t.toLowerCase() === "true";
    else if ((typeof was === "number" || was == null) && !isNaN(Number(t)) && /^-?\d/.test(t)) val = Number(t);
    else val = now;
    td.classList.add("rv-edited");
    Promise.resolve(onCellEdit(unit.rec, path, val)).then(() => { td.dataset.orig = now; unit.values[col.key] = val; });
  });
}
