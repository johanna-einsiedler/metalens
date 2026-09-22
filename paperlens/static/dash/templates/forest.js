// forest: one line per row — the estimate with its interval when bounds are bound. Slots:
// x (estimate), lower, upper (optional), label, color (optional). options.reference_line
// (default 0) is the dashed "no effect" line. Rows are capped (options.max_rows, default 60)
// and the cut is stated, never silent.
import { d3, svgEl, legend, xTitle, nodata, seriesCls, strokeCls, wireMark, colLabel, trunc, widthOf, labelOf } from "/static/dash/chart.js";

export function render(root, block, ctx) {
  const b = block.bindings || {}, o = block.options || {};
  const xc = b.x && b.x.column, lo = b.lower && b.lower.column, hi = b.upper && b.upper.column;
  const cc = b.color && b.color.column;
  if (!xc) return nodata(root, "Choose the estimate column.");
  let pts = ctx.rows.filter((r) => typeof r.get(xc) === "number");
  if (!pts.length) return nodata(root);
  const sort = (block.transform || {}).sort;
  if (sort && sort.by) pts = [...pts].sort((a, c) => (sort.dir === "asc" ? 1 : -1) * ((a.get(xc) ?? 0) - (c.get(xc) ?? 0)));
  const max = o.max_rows || 60, total = pts.length;
  pts = pts.slice(0, max);
  const cats = cc ? [...new Set(pts.map((r) => String(r.get(cc) ?? "—")))] : [];
  const W = widthOf(root), rowH = 22, m = { top: 8, right: 24, bottom: 42, left: Math.min(460, Math.round(W * 0.4)) };
  const H = pts.length * rowH + m.top + m.bottom;
  const svg = svgEl(root, W, H, block.title);
  const ref = typeof o.reference_line === "number" ? o.reference_line : 0;
  const dom = d3.extent(pts.flatMap((r) => [r.get(xc), lo ? r.get(lo) : null, hi ? r.get(hi) : null].filter((v) => typeof v === "number")).concat([ref]));
  const x = d3.scaleLinear().domain(dom).nice().range([m.left, W - m.right]);
  const y = (i) => m.top + i * rowH + rowH / 2;
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(d3.axisBottom(x).ticks(6));
  svg.append("line").attr("class", "forest-ref").attr("x1", x(ref)).attr("x2", x(ref)).attr("y1", m.top).attr("y2", H - m.bottom);
  const g = svg.append("g").selectAll("g").data(pts).join("g").attr("transform", (r, i) => `translate(0,${y(i)})`);
  g.append("text").attr("class", "forest-label").attr("x", m.left - 10).attr("dy", "0.32em").attr("text-anchor", "end")
    .text((r) => labelOf(r, b.label)).each(trunc(Math.round(m.left / 7)));
  const ci = (r) => cc ? cats.indexOf(String(r.get(cc) ?? "—")) : -1;
  g.filter((r) => lo && hi && typeof r.get(lo) === "number" && typeof r.get(hi) === "number").append("line")
    .attr("class", (r) => (cc ? `forest-line ${strokeCls(ci(r))}` : "forest-line"))
    .attr("x1", (r) => x(r.get(lo))).attr("x2", (r) => x(r.get(hi))).attr("y1", 0).attr("y2", 0);
  const dots = g.append("rect").attr("class", (r) => (cc ? seriesCls(ci(r)) : "forest-dot"))
    .attr("x", (r) => x(r.get(xc)) - 4).attr("y", -4).attr("width", 8).attr("height", 8);
  const markOf = (r) => ({ rows: [r], label: labelOf(r, b.label),
    values: [{ slot: "x", col: xc, label: colLabel(ctx, xc), value: r.get(xc) }]
      .concat(lo && hi ? [{ slot: "lower", col: lo, label: "lower", value: r.get(lo) }, { slot: "upper", col: hi, label: "upper", value: r.get(hi) }] : []) });
  wireMark(dots, ctx, markOf);
  xTitle(svg, W, H, colLabel(ctx, xc));
  if (cc) legend(root, cats, colLabel(ctx, cc));
  if (total > pts.length) { const p = document.createElement("p"); p.className = "dash-fig-n"; p.textContent = `Showing ${pts.length} of ${total} rows; narrow the block with a filter to see the rest.`; root.appendChild(p); }
  return { n: pts.length, marks: pts.map(markOf), used: pts };
}
