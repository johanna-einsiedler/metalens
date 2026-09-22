// scatter: one point per row. Slots: x, y (measures), color (dimension, optional), label.
// options.diagonal draws y = x, the line that matters when both axes share a scale.
import { d3, svgEl, legend, xTitle, yTitle, nodata, seriesCls, wireMark, colLabel, widthOf, labelOf, numScale, logTicks, logWorthIt } from "/static/dash/chart.js";

export function render(root, block, ctx) {
  const b = block.bindings || {};
  const xc = b.x && b.x.column, yc = b.y && b.y.column, cc = b.color && b.color.column;
  if (!xc || !yc) return nodata(root, "Choose the two columns to compare.");
  const pts = ctx.rows.filter((r) => typeof r.get(xc) === "number" && typeof r.get(yc) === "number");
  if (!pts.length) return nodata(root);
  const cats = cc ? [...new Set(pts.map((r) => String(r.get(cc) ?? "—")))] : [];
  const W = widthOf(root), H = Math.round(Math.min(420, Math.max(300, W * 0.56))), m = { top: 12, right: 16, bottom: 46, left: 60 };
  const svg = svgEl(root, W, H, block.title);
  const o = block.options || {}, xs = pts.map((r) => r.get(xc)), ys = pts.map((r) => r.get(yc));
  const both = o.diagonal ? xs.concat(ys) : null;          // y = x needs one scale on both axes
  const X = numScale(both || xs, o.log_x || (o.diagonal && o.log_y)), Y = numScale(both || ys, o.log_y || (o.diagonal && o.log_x));
  const x = X.scale.range([m.left, W - m.right]), y = Y.scale.range([H - m.bottom, m.top]), xd = x.domain();
  svg.append("g").attr("class", "grid").attr("transform", `translate(${m.left},0)`).call(logTicks(d3.axisLeft(y), Y.log, 5).tickSize(-(W - m.left - m.right)).tickFormat(""));
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(logTicks(d3.axisBottom(x), X.log, 6));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(logTicks(d3.axisLeft(y), Y.log, 5));
  if ((block.options || {}).diagonal) {
    svg.append("line").attr("class", "forest-ref").attr("x1", x(xd[0])).attr("y1", y(xd[0])).attr("x2", x(xd[1])).attr("y2", y(xd[1]));
  }
  const dots = svg.append("g").selectAll("circle").data(pts).join("circle")
    .attr("class", (r) => (cc ? seriesCls(cats.indexOf(String(r.get(cc) ?? "—"))) : "mark"))
    .attr("cx", (r) => x(r.get(xc))).attr("cy", (r) => y(r.get(yc))).attr("r", 4.2);
  const markOf = (r) => ({ rows: [r], label: labelOf(r, b.label),
    values: [{ slot: "x", col: xc, label: colLabel(ctx, xc), value: r.get(xc) }, { slot: "y", col: yc, label: colLabel(ctx, yc), value: r.get(yc) }] });
  wireMark(dots, ctx, markOf);
  xTitle(svg, W, H, colLabel(ctx, xc) + (X.log ? ", log scale" : "")); yTitle(svg, H, colLabel(ctx, yc) + (Y.log ? ", log scale" : ""));
  if (cc) legend(root, cats, colLabel(ctx, cc));
  const hints = [];
  if (!X.log && logWorthIt(xs)) hints.push({ text: `${colLabel(ctx, xc)} spans several orders of magnitude and most points sit at the low end.`, option: "log_x", value: true, action: "Use a log scale (horizontal)" });
  if (!Y.log && logWorthIt(ys)) hints.push({ text: `${colLabel(ctx, yc)} spans several orders of magnitude and most points sit at the low end.`, option: "log_y", value: true, action: "Use a log scale (vertical)" });
  return { n: pts.length, marks: pts.map(markOf), used: pts, hints };
}
