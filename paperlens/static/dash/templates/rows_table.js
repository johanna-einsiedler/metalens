// rows_table: the raw rows behind a dashboard. bindings.columns = {columns: [names]} (default:
// study + the first measures and dimensions). Every value cell is traceable like a chart mark.
import { esc } from "/static/grammar.js";
import { fmt, nodata, colLabel } from "/static/dash/chart.js";

export function render(root, block, ctx) {
  const want = ((block.bindings || {}).columns || {}).columns;
  // a sensible selection: what the row itself holds first (its own values, then its sub-entry's), never the system columns
  const depth = { row: 0, derived: 1, child: 2, entry: 3 };
  const auto = ctx.table.columns.filter((c) => c.scope !== "system" && c.n > 0 && (c.roles || []).some((r) => r === "measure" || r === "dimension" || r === "identifier" || r === "label"))
    .sort((a, b) => (depth[a.scope] ?? 9) - (depth[b.scope] ?? 9)).slice(0, 8).map((c) => c.name);
  const cols = ["_study"].concat((Array.isArray(want) && want.length ? want : auto).filter((n) => n !== "_study" && ctx.table.col(n)));
  const max = (block.options || {}).max_rows || 100;
  const rows = ctx.rows.slice(0, max);
  if (!rows.length) return nodata(root);
  const wrap = document.createElement("div"); wrap.className = "tp-scroll dash-rows";
  wrap.innerHTML = `<table class="tp-table"><thead><tr>${cols.map((n) => `<th>${esc(colLabel(ctx, n))}</th>`).join("")}<th>Status</th></tr></thead><tbody>`
    + rows.map((r, i) => `<tr>${cols.map((n) => {
      const c = ctx.table.col(n), v = r.get(n);
      const traceable = n !== "_study" && v != null && v !== "";
      return `<td class="${c && (c.type === "number" || c.type === "integer") ? "tp-num" : ""}">`
        + (traceable ? `<button type="button" class="cell-trace${r.rec.status !== "verified" ? " unverified" : ""}" data-r="${i}" data-c="${esc(n)}">${esc(fmt(v))}${r.corrected(n) ? " ✎" : ""}</button>` : esc(fmt(v))) + `</td>`;
    }).join("")}<td><span class="st-pill st-${esc(r.rec.status)}">${esc(r.rec.status)}</span></td></tr>`).join("")
    + `</tbody></table>`;
  root.appendChild(wrap);
  const markOf = (el) => { const r = rows[+el.dataset.r], n = el.dataset.c; return { rows: [r], label: String(r.get("_study") ?? ""), values: [{ slot: "value", col: n, label: colLabel(ctx, n), value: r.get(n) }] }; };
  wrap.querySelectorAll(".cell-trace").forEach((el) => {
    el.addEventListener("mouseenter", (ev) => ctx.ev.hover(markOf(el), ev, el));
    el.addEventListener("focus", (ev) => ctx.ev.hover(markOf(el), ev, el));
    el.addEventListener("mousemove", (ev) => ctx.ev.move(ev));
    el.addEventListener("mouseleave", () => ctx.ev.leave());
    el.addEventListener("blur", () => ctx.ev.leave());
    el.addEventListener("click", () => ctx.ev.pin(markOf(el)));
  });
  if (ctx.rows.length > rows.length) { const p = document.createElement("p"); p.className = "dash-fig-n"; p.textContent = `Showing ${rows.length} of ${ctx.rows.length} rows.`; root.appendChild(p); }
  return { n: rows.length, marks: [] };
}
