// Key numbers and the summary table. They are computed here from the rows, never written by
// a model, and they trace like any mark: the number is a button that pins the rows behind it.
import { esc } from "/static/grammar.js";
import { fmt, nodata, colLabel } from "/static/dash/chart.js";
import { groupBy, studiesOf } from "/static/dash/table.js";

const colOf = (b, slot) => b[slot] && b[slot].column;
// the caption under the number is written from the column names unless the block brings its own
function tile(root, ctx, { value, unit, note, rows, label, col }, block) {
  note = ((block && block.options) || {}).note || note;
  // in the composer the caption is typed right here, in the preview (ctx.edit); the tile is then
  // not a button, so the text can take the caret
  const el = document.createElement(ctx.edit ? "div" : "button");
  if (!ctx.edit) el.type = "button";
  el.className = "stat-tile";
  el.innerHTML = `<span class="stat-v">${esc(value)}</span>${unit ? `<span class="stat-u">${esc(unit)}</span>` : ""}`
    + `<span class="stat-n"${ctx.edit ? ' contenteditable="plaintext-only" spellcheck="false" title="click to write your own caption; empty it to get the automatic one back"' : ""}>${esc(note || "")}</span>`;
  if (ctx.edit) {
    const n = el.querySelector(".stat-n"), before = n.textContent;
    n.addEventListener("click", (ev) => ev.stopPropagation());          // typing, not pinning the evidence
    n.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); n.blur(); } if (ev.key === "Escape") { n.textContent = before; n.blur(); } });
    n.addEventListener("blur", () => { const t = n.textContent.replace(/\s+/g, " ").trim(); if (t !== before) ctx.edit.note(t); });
  }
  root.appendChild(el);
  const mark = { rows, label, values: [{ slot: "value", col: col || null, label, value }] };
  el.addEventListener("mouseenter", (ev) => ctx.ev.hover(mark, ev, el)); el.addEventListener("focus", (ev) => ctx.ev.hover(mark, ev, el));
  el.addEventListener("mousemove", (ev) => ctx.ev.move(ev));
  el.addEventListener("mouseleave", () => ctx.ev.leave()); el.addEventListener("blur", () => ctx.ev.leave());
  el.addEventListener("click", () => ctx.ev.pin(mark));
  return { n: rows.length, marks: [] };
}

export function statCount(root, block, ctx) {
  const c = colOf(block.bindings || {}, "column");
  if (!c) return tile(root, ctx, { value: String(ctx.rows.length), note: `${ctx.table.unit.label.toLowerCase()} rows`, rows: ctx.rows, label: block.title }, block);
  const rows = ctx.rows.filter((r) => r.get(c) != null && r.get(c) !== "");
  const col = ctx.table.col(c), isId = col && !c.startsWith("_") && (col.roles || []).includes("identifier");
  const within = c === "_entry" || (isId && col.scope === "entry") ? (r) => `${r.rec.paper}|` : isId ? (r) => `${r.rec.id}|` : () => "";
  return tile(root, ctx, { value: String(new Set(rows.map((r) => within(r) + String(r.get(c)))).size), note: c === "_study" ? "studies" : `distinct values of ${colLabel(ctx, c)}`, rows, label: block.title }, block);
}
export function statAggregate(root, block, ctx) {
  const b = block.bindings || {}, c = colOf(b, "value"), agg = (b.value && b.value.agg) || "median";
  if (!c) return nodata(root, "Choose a measure.");
  const rows = ctx.rows.filter((r) => typeof r.get(c) === "number");
  if (!rows.length) return nodata(root);
  const g = groupBy(rows, [], c, agg)[0];
  return tile(root, ctx, { value: fmt(g.value), note: `${agg} of ${colLabel(ctx, c)} · ${rows.length} rows, ${studiesOf(rows)} studies`, rows, label: block.title, col: c }, block);
}
export function statShare(root, block, ctx) {
  const c = colOf(block.bindings || {}, "column"), want = String((block.options || {}).equals ?? "");
  if (!c) return nodata(root, "Choose a column.");
  const rows = ctx.rows.filter((r) => r.get(c) != null && r.get(c) !== "");
  if (!rows.length) return nodata(root);
  const shown = rows.find((r) => String(r.raw(c)) === want);
  const hit = rows.filter((r) => String(r.raw(c)) === want);
  return tile(root, ctx, { value: String(Math.round(100 * hit.length / rows.length)), unit: "%", note: `${hit.length} of ${rows.length} rows: ${colLabel(ctx, c).toLowerCase()} = ${shown ? shown.get(c) : want}`, rows: hit, label: block.title }, block);
}

export function summaryTable(root, block, ctx) {
  const b = block.bindings || {}, gc = colOf(b, "group"), vc = colOf(b, "value");
  if (!gc || !vc) return nodata(root, "Choose a grouping and a measure.");
  const rows = ctx.rows.filter((r) => typeof r.get(vc) === "number");
  if (!rows.length) return nodata(root);
  const groups = groupBy(rows, [gc], vc, "mean").sort((a, c) => c.n - a.n);
  const stat = (g, agg) => groupBy(g.rows, [], vc, agg)[0].value;
  const wrap = document.createElement("div"); wrap.className = "tp-scroll";
  wrap.innerHTML = `<table class="tp-table"><thead><tr><th>${esc(colLabel(ctx, gc))}</th><th>Rows</th><th>Studies</th><th>Mean</th><th>Median</th><th>Min</th><th>Max</th></tr></thead><tbody>`
    + groups.map((g, i) => `<tr><td>${esc(g.key[0])}</td><td class="tp-num">${g.n}</td><td class="tp-num">${studiesOf(g.rows)}</td>`
      + `<td class="tp-num"><button type="button" class="cell-trace" data-g="${i}">${esc(fmt(g.value))}</button></td>`
      + ["median", "min", "max"].map((a) => `<td class="tp-num">${esc(fmt(stat(g, a)))}</td>`).join("") + `</tr>`).join("") + `</tbody></table>`;
  root.appendChild(wrap);
  wrap.querySelectorAll(".cell-trace").forEach((el) => {
    const g = groups[+el.dataset.g];
    const mark = { rows: g.rows, label: `${colLabel(ctx, gc)}: ${g.key[0]}`, values: [{ slot: "value", col: vc, label: `mean of ${colLabel(ctx, vc)}`, value: g.value }] };
    el.addEventListener("mouseenter", (ev) => ctx.ev.hover(mark, ev, el)); el.addEventListener("mousemove", (ev) => ctx.ev.move(ev));
    el.addEventListener("mouseleave", () => ctx.ev.leave()); el.addEventListener("click", () => ctx.ev.pin(mark));
  });
  return { n: rows.length, marks: [] };
}
