// Templates whose marks stand for SEVERAL rows (a bar, a bin, a heat-map cell) or that spread
// rows along a category (dot strip). An aggregated mark keeps its member rows, so hover shows
// how many rows and studies it rests on and a click lists every one with its evidence.
import { d3, svgEl, legend, xTitle, yTitle, nodata, seriesCls, wireMark, colLabel, trunc, widthOf, labelOf, fmt, numScale, logTicks, logWorthIt } from "/static/dash/chart.js";
import { groupBy } from "/static/dash/table.js";

const colOf = (b, slot) => b[slot] && b[slot].column;
const str = (v) => (v == null || v === "" ? "—" : String(v));
const byNumberThenText = (a, b) => (Number.isFinite(+a) && Number.isFinite(+b) ? +a - +b : String(a).localeCompare(String(b)));

// dot_strip: x = measure, y = category (one dot per row)
export function dotStrip(root, block, ctx) {
  const b = block.bindings || {}, xc = colOf(b, "x"), yc = colOf(b, "y"), cc = colOf(b, "color");
  if (!xc || !yc) return nodata(root, "Choose a value and a group.");
  const pts = ctx.rows.filter((r) => typeof r.get(xc) === "number");
  if (!pts.length) return nodata(root);
  const groups = [...new Set(pts.map((r) => str(r.get(yc))))].sort(byNumberThenText).slice(0, 60);
  const cats = cc ? [...new Set(pts.map((r) => str(r.get(cc))))] : [];
  const W = widthOf(root), rowH = 22, m = { top: 8, right: 20, bottom: 44, left: Math.min(240, Math.round(W * 0.28)) };
  const H = groups.length * rowH + m.top + m.bottom;
  const svg = svgEl(root, W, H, block.title);
  const ref = (block.options || {}).reference_line;
  const X = numScale(pts.map((r) => r.get(xc)), (block.options || {}).log_x, [ref]), x = X.scale.range([m.left, W - m.right]);
  const y = d3.scalePoint().domain(groups).range([m.top + rowH / 2, H - m.bottom - rowH / 2]);
  svg.append("g").attr("class", "grid").selectAll("line").data(groups).join("line").attr("x1", m.left).attr("x2", W - m.right).attr("y1", (g) => y(g)).attr("y2", (g) => y(g));
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(logTicks(d3.axisBottom(x), X.log, 6));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).tickSize(0)).call((g) => g.selectAll(".tick text").each(trunc(Math.round(m.left / 7.2))));
  if (typeof ref === "number" && (!X.log || ref > 0)) svg.append("line").attr("class", "forest-ref").attr("x1", x(ref)).attr("x2", x(ref)).attr("y1", m.top).attr("y2", H - m.bottom);
  const shown = pts.filter((r) => groups.includes(str(r.get(yc))));
  const dots = svg.append("g").selectAll("circle").data(shown).join("circle")
    .attr("class", (r) => (cc ? seriesCls(cats.indexOf(str(r.get(cc)))) : "mark"))
    .attr("cx", (r) => x(r.get(xc))).attr("cy", (r) => y(str(r.get(yc)))).attr("r", 3.8);
  const markOf = (r) => ({ rows: [r], label: `${labelOf(r, b.label)} · ${colLabel(ctx, yc)} ${str(r.get(yc))}`,
    values: [{ slot: "x", col: xc, label: colLabel(ctx, xc), value: r.get(xc) }] });
  wireMark(dots, ctx, markOf);
  xTitle(svg, W, H, colLabel(ctx, xc) + (X.log ? ", log scale" : ""));
  if (cc) legend(root, cats, colLabel(ctx, cc));
  const hints = !X.log && logWorthIt(shown.map((r) => r.get(xc)))
    ? [{ text: `${colLabel(ctx, xc)} spans several orders of magnitude and most dots sit at the low end.`, option: "log_x", value: true, action: "Use a log scale" }] : [];
  return { n: shown.length, marks: shown.map(markOf), used: shown, hints };
}

// bar: y = category, x = count or aggregate of a measure
export function bar(root, block, ctx) {
  const b = block.bindings || {}, yc = colOf(b, "y"), xc = colOf(b, "x"), agg = xc ? (b.x.agg || "mean") : "count";
  if (!yc) return nodata(root, "Choose a category.");
  const rows = xc ? ctx.rows.filter((r) => typeof r.get(xc) === "number") : ctx.rows;
  let groups = groupBy(rows, [yc], xc, agg).filter((g) => g.value != null).sort((a, c) => c.value - a.value);
  if (!groups.length) return nodata(root);
  groups = groups.slice(0, (block.options || {}).max_bars || 20);
  const W = widthOf(root), rowH = 26, m = { top: 6, right: 52, bottom: 42, left: Math.min(260, Math.round(W * 0.32)) };
  const H = groups.length * rowH + m.top + m.bottom;
  const svg = svgEl(root, W, H, block.title);
  const x = d3.scaleLinear().domain([Math.min(0, d3.min(groups, (g) => g.value)), Math.max(0, d3.max(groups, (g) => g.value))]).nice().range([m.left, W - m.right]);
  const y = d3.scaleBand().domain(groups.map((g) => g.key[0])).range([m.top, H - m.bottom]).padding(0.25);
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(d3.axisBottom(x).ticks(5));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).tickSize(0)).call((g) => g.selectAll(".tick text").each(trunc(Math.round(m.left / 7.2))));
  const bars = svg.append("g").selectAll("rect").data(groups).join("rect").attr("class", "mark")
    .attr("x", (g) => Math.min(x(0), x(g.value))).attr("width", (g) => Math.abs(x(g.value) - x(0))).attr("y", (g) => y(g.key[0])).attr("height", y.bandwidth());
  svg.append("g").selectAll("text").data(groups).join("text").attr("class", "bar-val").attr("x", (g) => Math.max(x(0), x(g.value)) + 5)
    .attr("y", (g) => y(g.key[0]) + y.bandwidth() / 2).attr("dy", "0.32em").text((g) => fmt(g.value));
  const vlabel = xc ? `${agg} of ${colLabel(ctx, xc)}` : "rows";
  const markOf = (g) => ({ rows: g.rows, label: `${colLabel(ctx, yc)}: ${g.key[0]}`, values: [{ slot: "x", col: xc || null, label: vlabel, value: g.value }] });
  wireMark(bars, ctx, markOf);
  xTitle(svg, W, H, vlabel);
  return { n: rows.length, marks: [], used: rows };
}

// histogram: x = measure in bins
export function histogram(root, block, ctx) {
  const xc = colOf(block.bindings || {}, "x");
  if (!xc) return nodata(root, "Choose a value.");
  const rows = ctx.rows.filter((r) => typeof r.get(xc) === "number");
  if (!rows.length) return nodata(root);
  const W = widthOf(root), H = Math.round(Math.min(360, Math.max(260, W * 0.5))), m = { top: 10, right: 14, bottom: 46, left: 56 };
  const svg = svgEl(root, W, H, block.title);
  const x = d3.scaleLinear().domain(d3.extent(rows, (r) => r.get(xc))).nice().range([m.left, W - m.right]);
  const bins = d3.bin().domain(x.domain()).thresholds(x.ticks((block.options || {}).bins || 20)).value((r) => r.get(xc))(rows);
  const y = d3.scaleLinear().domain([0, d3.max(bins, (bn) => bn.length) || 1]).nice().range([H - m.bottom, m.top]);
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(d3.axisBottom(x).ticks(6));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5));
  const rects = svg.append("g").selectAll("rect").data(bins.filter((bn) => bn.length)).join("rect").attr("class", "mark")
    .attr("x", (bn) => x(bn.x0) + 1).attr("width", (bn) => Math.max(1, x(bn.x1) - x(bn.x0) - 1)).attr("y", (bn) => y(bn.length)).attr("height", (bn) => y(0) - y(bn.length));
  wireMark(rects, ctx, (bn) => ({ rows: [...bn], label: `${colLabel(ctx, xc)} from ${fmt(bn.x0)} to ${fmt(bn.x1)}`, values: [{ slot: "x", col: xc, label: "rows", value: bn.length }] }));
  xTitle(svg, W, H, colLabel(ctx, xc)); yTitle(svg, H, "rows");
  return { n: rows.length, marks: [], used: rows };
}

// heatmap: x, y = categories, value = aggregated measure; a diverging wash around zero
export function heatmap(root, block, ctx) {
  const b = block.bindings || {}, xc = colOf(b, "x"), yc = colOf(b, "y"), vc = colOf(b, "value"), agg = (b.value && b.value.agg) || "mean";
  if (!xc || !yc || !vc) return nodata(root, "Choose rows, columns and a cell value.");
  const rows = ctx.rows.filter((r) => typeof r.get(vc) === "number");
  const cells = groupBy(rows, [yc, xc], vc, agg).filter((g) => g.value != null);
  if (!cells.length) return nodata(root);
  const xs = [...new Set(cells.map((g) => g.key[1]))].sort(byNumberThenText), ys = [...new Set(cells.map((g) => g.key[0]))].sort(byNumberThenText);
  const W = widthOf(root), m = { top: 30, right: 16, bottom: 12, left: Math.min(220, Math.round(W * 0.24)) };
  const cellH = Math.max(16, Math.min(28, Math.round(520 / ys.length))), H = ys.length * cellH + m.top + m.bottom;
  const svg = svgEl(root, W, H, block.title);
  const x = d3.scaleBand().domain(xs).range([m.left, W - m.right]).padding(0.06), y = d3.scaleBand().domain(ys).range([m.top, H - m.bottom]).padding(0.06);
  const maxAbs = d3.max(cells, (g) => Math.abs(g.value)) || 1;
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${m.top})`).call(d3.axisTop(x).tickSize(0)).call((g) => g.selectAll(".tick text").each(trunc(Math.max(4, Math.round(x.bandwidth() / 7.2)))));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).tickSize(0)).call((g) => g.selectAll(".tick text").each(trunc(Math.round(m.left / 7.2))));
  const g = svg.append("g").selectAll("g").data(cells).join("g").attr("transform", (c) => `translate(${x(c.key[1])},${y(c.key[0])})`);
  const rect = g.append("rect").attr("class", (c) => (c.value < 0 ? "heat heat-neg" : "heat heat-pos"))
    .attr("width", x.bandwidth()).attr("height", y.bandwidth()).style("fill-opacity", (c) => 0.08 + 0.85 * Math.abs(c.value) / maxAbs);
  if (x.bandwidth() > 34) g.append("text").attr("class", "heat-val").attr("x", x.bandwidth() / 2).attr("y", y.bandwidth() / 2).attr("dy", "0.32em").attr("text-anchor", "middle").text((c) => fmt(c.value));
  wireMark(rect, ctx, (c) => ({ rows: c.rows, label: `${colLabel(ctx, yc)} ${c.key[0]} · ${colLabel(ctx, xc)} ${c.key[1]}`,
    values: [{ slot: "value", col: vc, label: c.rows.length > 1 ? `${agg} of ${colLabel(ctx, vc)}` : colLabel(ctx, vc), value: c.value }] }));
  return { n: rows.length, marks: [], used: rows };
}
