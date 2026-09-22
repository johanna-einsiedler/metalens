// Where a mark comes from. Row-level provenance (paper, dataset, model, date, status) is in
// the table already; the EVIDENCE of a cell (quote, page, source, kind of support) is fetched
// on demand in batches and cached. One controller per dashboard: hover → tooltip, click → a
// pinned panel listing every contributing row. Nothing here ever shows a PDF: public viewers
// get quotes, pages and source labels; only the owner gets the link into the review screen.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { showTip, hideTip } from "/static/tooltip.js";
import { fmt } from "/static/dash/chart.js";
import { studiesOf } from "/static/dash/table.js";

const keyOf = (row, col) => `${row.rec.id}|${row.path}|${col}`;
const STATUS = { verified: "verified by a human", unverified: "not yet verified", flagged: "flagged by a reviewer" };

export function createEvidence(table, panelHost) {
  const cache = new Map();          // cell key → evidence | Promise
  let hoverToken = 0, timer = null;

  async function fetchCells(pairs) {
    const need = pairs.filter(([r, c]) => !cache.has(keyOf(r, c)));
    for (let i = 0; i < need.length; i += 300) {
      const chunk = need.slice(i, i + 300);
      const cells = chunk.map(([r, c]) => ({ record_id: r.rec.id, path: r.path, column: c })), src = table.source || { dataset: table.dataset.id };
      const p = (src.dashboard ? api.dashboardEvidence(src.dashboard, table.unit.id, cells, src.view) : api.analysisEvidence(src.dataset, table.unit.id, cells, src.release))
        .then((res) => new Map((res.cells || []).map((x) => [`${x.record_id}|${x.path}|${x.column}`, x])))
        .catch(() => new Map());
      chunk.forEach(([r, c]) => cache.set(keyOf(r, c), p.then((m) => m.get(keyOf(r, c)) || { kind: "none", kind_label: "no evidence recorded", items: [] })));
    }
    return Promise.all(pairs.map(([r, c]) => cache.get(keyOf(r, c))));
  }
  const cellsOf = (mark, max) => mark.rows.slice(0, max).flatMap((r) => mark.values.filter((v) => v.col).map((v) => [r, v.col]));

  // Where a quote sits, in plain words: "Table 3, row “Few shot”, p. 5". Section names are left
  // out (the page is enough); the row is named by its printed label when the PDF layout gave one
  // (the paper's row NUMBER is not known: the index we hold is the position in the extraction).
  function whereText(kind, it) {
    const src = /^\s*section\b/i.test(it.source || "") ? "" : (it.source || "").split(",")[0].trim();
    const label = it.context && (it.context.row_label || "").trim();
    const inTable = !!label || /\btab(le|\.)/i.test(src);      // "row" only means something inside a table
    const row = kind === "row" && inTable ? (label ? `row “${label.slice(0, 40)}”` : "row") : "";
    return [src, row, `p. ${it.page ?? "?"}`].filter(Boolean).join(", ") + (kind === "entry" ? " (quoted for the whole entry)" : "");
  }

  // ── the pieces of provenance, as HTML ──────────────────────────────────────
  function evidenceHtml(ev, colLabel) {
    if (!ev) return "";
    if (ev.kind === "derived") {
      // inputs that rest on the same quote (one table row) are named together, once
      const groups = new Map();
      for (const i of ev.inputs || []) {
        const it = (i.items || [])[0];
        const k = it ? `${i.kind}|${it.page}|${it.source}|${it.snippet}` : `${i.kind}|none`;
        if (!groups.has(k)) groups.set(k, { ...i, labels: [] });
        groups.get(k).labels.push(i.label);
      }
      return `<div class="tt-ev"><span class="tt-where">Computed: ${esc(ev.formula || "")}</span></div>`
        + [...groups.values()].map((g) => evidenceHtml(g, `from ${g.labels.join(", ")}`)).join("");
    }
    const fix = ev.corrected ? `<div class="tt-fix">human-corrected (was ${esc(fmt(ev.corrected.original_value))})</div>` : "";
    const lead = colLabel ? `${esc(colLabel)}: ` : "";
    if (!ev.items || !ev.items.length) return `<div class="tt-ev"><span class="tt-where">${lead}${esc(ev.kind_label || "no evidence recorded")}</span></div>${fix}`;
    const it = ev.items[0], q = quoteHtml(it, ev._values);
    return `<div class="tt-ev"><span class="tt-where">${lead}${esc(whereText(ev.kind, it))}</span>${q.html}</div>${fix}`;
  }
  function rowHeadHtml(row) {
    const p = row.paper, r = row.rec;
    return `<div class="tt-study">${esc(p.study || p.title || "Untitled")}</div>`
      + `<div class="tt-sub">${esc(p.title || "")}${p.journal ? ` · <i>${esc(p.journal)}</i>` : ""}</div>`
      + `<div class="tt-meta"><span class="st-pill st-${esc(r.status)}">${esc(STATUS[r.status] || r.status)}</span> · ${esc(r.entry_label || "")}</div>`;
  }
  // what this row's numbers measure (the unit lives in the row, not on the axis)
  function metricHtml(row, cols) {
    const c = cols.map((v) => table.col(v.col)).find((x) => x && x.unit_by); if (!c) return "";
    const text = (c.unit_detail && row.get(c.unit_detail)) || row.get(c.unit_by);
    return text ? `<div class="tt-meta"><b>Metric:</b> ${esc(String(text).slice(0, 200))}</div>` : "";
  }
  // A quoted TABLE ROW with what its numbers mean: the table's caption and the column header
  // printed above each number (read from the PDF layout on the server, best effort). This is
  // what makes "Few Shot 100 56 30" readable. Falls back to the plain quote without context.
  function quoteHtml(it, values) {
    const ctx = it && it.context;
    if (!ctx || !(ctx.cells || []).length || !ctx.cells.some((c) => c.header)) {
      const m = markNumbers((it.snippet || "").slice(0, 260), values || []);
      return { html: `<div class="tt-quote">“${m.html}${(it.snippet || "").length > 260 ? "…" : ""}”</div>`, missing: m.missing };
    }
    const found = new Set();
    const cell = (c) => { const m = markNumbers(c.text, values || []); (values || []).forEach((v) => { if (!m.missing.includes(v.label)) found.add(v.label); }); return m.html; };
    const html = (ctx.caption ? `<div class="tt-cap">${esc(ctx.caption)}</div>` : "")
      + `<table class="tt-row"><tr><th>${esc(ctx.stub || "")}</th>${ctx.cells.map((c) => `<th>${esc(c.header || "")}</th>`).join("")}</tr>`
      + `<tr><td>${esc((ctx.row_label || "").trim())}</td>${ctx.cells.map((c) => `<td>${cell(c)}</td>`).join("")}</tr></table>`;
    return { html, missing: (values || []).filter((v) => typeof v.value === "number" && !found.has(v.label)).map((v) => v.label) };
  }
  // the plotted numbers, marked inside the quote; a value the quote does not contain is said so
  function markNumbers(snippet, values) {
    let html = esc(snippet); const missing = [];
    for (const v of values) {
      if (typeof v.value !== "number") continue;
      const forms = [...new Set([String(v.value), String(v.value).replace(/^0\./, "."), v.value.toFixed(1), v.value.toFixed(2)])];
      const re = new RegExp(`(^|[^0-9.])(${forms.map((f) => f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![0-9])`);
      if (re.test(html)) html = html.replace(re, "$1<mark>$2</mark>"); else missing.push(v.label);
    }
    return { html, missing };
  }
  function singleHtml(mark, evs) {
    const row = mark.rows[0];
    // a value shown on another scale than printed (a proportion drawn as a percentage) says so,
    // and the quote is searched for the number as the paper printed it
    const printed = (v) => (v.col && row.raw && typeof row.raw(v.col) === "number" && typeof v.value === "number" && row.raw(v.col) !== v.value ? row.raw(v.col) : null);
    const vals = mark.values.map((v) => `<span class="tt-val"><span class="tt-slot">${esc(v.label)}</span> ${esc(fmt(v.value))}`
      + `${printed(v) != null ? ` <span class="tt-slot">(${esc(fmt(printed(v)))} in the paper)</span>` : ""}`
      + `${v.col && row.corrected(v.col) ? ' <span class="tt-corr" title="human-corrected">✎</span>' : ""}</span>`).join("");
    const cols = mark.values.filter((v) => v.col).map((v) => (printed(v) != null ? { ...v, value: printed(v) } : v));
    // values that rest on the SAME quote (two cells of one table row) are shown together, once
    const shared = evs && cols.length > 1 && evs.every((e) => e && e.kind !== "derived" && e.kind !== "none" && (e.items || []).length
      && e.kind === evs[0].kind && JSON.stringify(e.items[0]) === JSON.stringify(evs[0].items[0]));
    if (shared) {
      const it = evs[0].items[0], m = quoteHtml(it, cols);
      return `<div class="tt"><div class="tt-vals">${vals}</div>${rowHeadHtml(row)}${metricHtml(row, cols)}`
        + `<div class="tt-ev"><span class="tt-where">${esc(whereText(evs[0].kind, it))}</span>${m.html}`
        + `${m.missing.length ? `<div class="tt-fix">Not found in the quote: ${esc(m.missing.join(", "))}.</div>` : ""}</div></div>`;
    }
    if (evs && cols.length > 1 && evs.every((e) => e && e.kind === "none" && !e.corrected)) {
      return `<div class="tt"><div class="tt-vals">${vals}</div>${rowHeadHtml(row)}<div class="tt-ev"><span class="tt-where">No evidence recorded: `
        + `the dataset holds no quote for these values, their row or their entry.</span></div></div>`;
    }
    return `<div class="tt"><div class="tt-vals">${vals}</div>${rowHeadHtml(row)}${metricHtml(row, cols)}`
      + (evs ? [...new Set(cols.map((v, k) => evidenceHtml(evs[k] && evs[k].kind !== "derived" ? { ...evs[k], _values: [v] } : evs[k], "")))].join("")   // identical support shown once
             : `<div class="tt-ev muted">loading evidence…</div>`) + `</div>`;
  }
  function groupHtml(mark) {
    const ver = mark.rows.filter((r) => r.rec.status === "verified").length;
    const top = mark.rows.slice(0, 3).map((r) => `<li>${esc(r.paper.study || "")}${mark.values[0] && mark.values[0].col ? `: ${esc(fmt(r.get(mark.values[0].col)))}` : ""}</li>`).join("");
    return `<div class="tt"><div class="tt-vals">${mark.values.map((v) => `<span class="tt-val"><span class="tt-slot">${esc(v.label)}</span> ${esc(fmt(v.value))}</span>`).join("")}</div>`
      + `<div class="tt-study">${esc(mark.label)}</div>`
      + `<div class="tt-meta">${mark.rows.length} rows from ${studiesOf(mark.rows)} stud${studiesOf(mark.rows) === 1 ? "y" : "ies"} · ${ver} verified</div>`
      + `<ul class="tt-list">${top}</ul><div class="tt-meta">click for all ${mark.rows.length}</div></div>`;
  }

  // ── hover / pin ────────────────────────────────────────────────────────────
  let last = { x: 0, y: 0 };
  const where = (ev, node) => {
    if (ev && ev.clientX) return { x: ev.clientX, y: ev.clientY };
    const b = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;   // keyboard focus
    return b ? { x: b.left + b.width / 2, y: b.top } : last;
  };
  function hover(mark, ev, node) {
    const token = ++hoverToken; last = where(ev, node);
    if (mark.rows.length !== 1) { showTip(groupHtml(mark), last.x, last.y); return; }
    showTip(singleHtml(mark, null), last.x, last.y);
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const evs = await fetchCells(cellsOf(mark, 1));
      if (token === hoverToken) showTip(singleHtml(mark, evs), last.x, last.y);
    }, 120);
  }
  const move = (ev) => { last = where(ev); };
  const leave = () => { hoverToken++; clearTimeout(timer); hideTip(); };

  async function pin(mark) {
    if (!panelHost) return;
    hideTip();
    const rows = mark.rows.slice(0, 200), cols = mark.values.filter((v) => v.col);
    panelHost.innerHTML = `<div class="trace-panel"><div class="tp-head"><b>${esc(mark.label)}</b>`
      + `<span class="muted">${mark.rows.length} row${mark.rows.length === 1 ? "" : "s"} · ${studiesOf(mark.rows)} stud${studiesOf(mark.rows) === 1 ? "y" : "ies"}</span>`
      + `<button class="tp-x" title="close" aria-label="close">✕</button></div><div class="tp-body tp-scroll"><p class="muted" style="padding:12px 14px;margin:0">loading evidence…</p></div></div>`;
    panelHost.querySelector(".tp-x").onclick = () => { panelHost.innerHTML = ""; };
    panelHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const evs = await fetchCells(rows.flatMap((r) => cols.map((v) => [r, v.col])));
    const showEv = cols.length > 0;                        // a count of rows has no cell to cite
    const head = `<tr><th>Study</th>${cols.map((v) => `<th>${esc(v.label)}</th>`).join("")}<th>Status</th><th>Extracted</th>${showEv ? "<th>Evidence</th>" : ""}${table.viewer.owner ? "<th></th>" : ""}</tr>`;
    const body = rows.map((r, i) => {
      const mine = cols.map((_, k) => evs[i * cols.length + k]);
      const first = mine.find((e) => e && (e.items || []).length) || mine[0] || {};
      const flat = first.kind === "derived" ? ((first.inputs || []).find((x) => (x.items || []).length) || {}) : first;
      const it = (flat.items || [])[0];
      return `<tr><td>${esc(r.paper.study || "")}<div class="muted">${esc(r.rec.entry_label || "")}</div></td>`
        + cols.map((v) => `<td class="tp-num">${esc(fmt(r.get(v.col)))}${r.corrected(v.col) ? " ✎" : ""}</td>`).join("")
        + `<td><span class="st-pill st-${esc(r.rec.status)}">${esc(r.rec.status)}</span></td>`
        + `<td class="muted">${esc(r.rec.model || "")}<div>${esc(r.rec.extracted_at || "")}</div></td>`
        + (!showEv ? "" : `<td class="tp-ev">${it ? `<span class="tt-where">${esc(whereText(flat.kind, it))}</span>${quoteHtml(it, cols).html}`
          : `<span class="tt-where">${esc((first && first.kind_label) || "no evidence recorded")}</span>`}</td>`)
        + (table.viewer.owner && r.rec.document_id ? `<td><a href="/workspace?doc=${encodeURIComponent(r.rec.document_id)}&rec=${encodeURIComponent(r.rec.id)}">Open in review</a></td>` : table.viewer.owner ? "<td></td>" : "")
        + `</tr>`;
    }).join("");
    panelHost.querySelector(".tp-body").innerHTML = `<table class="tp-table"><thead>${head}</thead><tbody>${body}</tbody></table>`
      + (mark.rows.length > rows.length ? `<p class="muted" style="padding:8px 14px;margin:0">showing the first ${rows.length} of ${mark.rows.length}</p>` : "");
  }

  // small figures warm the cache so the first hover is instant
  const prefetch = (marks) => { const cells = marks.filter((m) => m.rows.length === 1).flatMap((m) => cellsOf(m, 1)); if (cells.length && cells.length <= 300) fetchCells(cells); };
  return { hover, move, leave, pin, prefetch, fetchCells };
}
