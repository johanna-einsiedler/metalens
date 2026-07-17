// Cross-record spreadsheet grid: one row per record, one column per field, with
// sub_view group headers over the columns and per-cell evidence linking. Renders
// the wide (~60-column) shape that the per-record card stack can't show at a glance.
// The renderer is dumb: it lays out records × columns and emits cell/row events; the
// caller (workspace or dataset) owns highlight/navigation via the callbacks.
import { esc } from "/static/grammar.js";

const SKIP = new Set(["evidence", "extraction_confidence"]);

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
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

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
 *   records     [{id, entry_index, field_values, verification_status}]
 *   subViews    [{label, include_keys}]
 *   evidenceFor (record) => [{i, page, path}]   // path pre-stripped; default () => []
 *   onCellClick (record, colKey, coveringOrNull, event) => void
 *   onRowClick  (record) => void                // optional (e.g. open in workspace)
 *   maxRows     number                          // cap; default 500
 */
export function renderGrid(host, opts) {
  const { records = [], subViews = [], evidenceFor = () => [],
          onCellClick, onRowClick, maxRows = 500 } = opts || {};
  if (!records.length) { host.innerHTML = '<p class="muted">No records to show.</p>'; return; }
  const cols = columnsFor(records, subViews);
  const shown = records.slice(0, maxRows);
  const spans = groupSpans(cols);

  const grpRow = `<tr class="grid-grp"><th class="grid-idx"></th>`
    + spans.map((g) => `<th colspan="${g.span}">${esc(g.label)}</th>`).join("") + `</tr>`;
  const colRow = `<tr class="grid-col"><th class="grid-idx">#</th>`
    + cols.map((c) => `<th title="${esc(c.key)}">${esc(c.key)}</th>`).join("") + `</tr>`;

  const body = shown.map((r) => {
    const recEv = evidenceFor(r) || [];
    const idx = (r.entry_index != null ? r.entry_index : "");
    const st = r.verification_status || "";
    const tds = cols.map((c) => {
      const cov = covering(c.key, recEv);
      const linked = cov ? " grid-linked" : "";
      const val = cellText((r.field_values || {})[c.key]);
      return `<td class="grid-cell${linked}" data-col="${esc(c.key)}"`
        + (cov ? ` data-eid="${cov.i}" data-page="${cov.page}"` : "")
        + `>${esc(val)}</td>`;
    }).join("");
    return `<tr data-rid="${esc(r.id || "")}"><td class="grid-idx" title="${esc(st)}">`
      + `${esc(String(idx))}${st ? `<span class="grid-st ${esc(st)}"></span>` : ""}</td>${tds}</tr>`;
  }).join("");

  const note = records.length > shown.length
    ? `<p class="muted grid-note">Showing ${shown.length} of ${records.length} rows.</p>` : "";

  host.innerHTML = `<div class="grid-wrap"><table class="rec-grid">`
    + `<thead>${grpRow}${colRow}</thead><tbody>${body}</tbody></table></div>${note}`;

  // wire cells + rows
  host.querySelectorAll("tr[data-rid]").forEach((tr) => {
    const rec = records.find((x) => String(x.id) === tr.dataset.rid);
    if (!rec) return;
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
    });
  });
}
