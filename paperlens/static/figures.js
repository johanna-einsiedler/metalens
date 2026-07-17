// D3 dashboard renderer. d3 v7 is loaded as a global via a classic <script> tag
// (vendor/d3.v7.min.js) that runs before this deferred module. Reads a FigureSpec
// + tidy rows, aggregates client-side, and draws SVG; all colour comes from
// theme.css tokens via CSS classes (keeps the no-raw-hex gate green).
import { esc } from "/static/grammar.js";

const d3 = window.d3;

// ── row access ───────────────────────────────────────────────────────────────
function valueOf(row, name) {
  if (name == null) return undefined;
  if (name !== "field_values" && Object.prototype.hasOwnProperty.call(row, name)) return row[name];
  let cur = row.field_values || {};
  for (const part of String(name).split(".")) {
    if (cur && typeof cur === "object" && part in cur) cur = cur[part];
    else return undefined;
  }
  return cur;
}
function num(v) {
  if (typeof v === "number") return Number.isFinite(v) ? v : undefined;
  if (typeof v === "string") { const n = parseFloat(v.replace(/[,%\s]/g, "")); return Number.isFinite(n) ? n : undefined; }
  return undefined;
}
const label = (v) => (v == null || v === "" ? "(none)" : String(v));
function fmt(v) {
  if (v == null || !Number.isFinite(v)) return "—";
  return Number.isInteger(v) ? String(v) : d3.format("~r")(Number(v.toPrecision(4)));
}
const seriesCls = (i) => `series-${(i % 8) + 1}`;
const strokeCls = (i) => `stroke-${(i % 8) + 1}`;

// ── aggregation ──────────────────────────────────────────────────────────────
function reduce(rs, yVar, agg) {
  if (agg === "count" || !yVar) return rs.length;
  const vals = rs.map((r) => num(valueOf(r, yVar))).filter((v) => v != null);
  if (!vals.length) return null;              // no numeric values → misconfigured, not zero
  if (agg === "sum") return d3.sum(vals);
  if (agg === "median") return d3.median(vals);
  return d3.mean(vals);
}
function aggregateSeries(rows, groupVar, yVar, agg, sort = true) {
  const groups = d3.group(rows, (r) => label(valueOf(r, groupVar)));
  const out = [];
  for (const [key, rs] of groups) out.push({
    key, value: reduce(rs, yVar, agg), n: rs.length,
    record_ids: rs.map((r) => r.record_id), document_ids: rs.map((r) => r.document_id),
  });
  if (sort) out.sort((a, b) => d3.descending(a.value, b.value));
  return out;
}

// ── mark ↔ record traceability ───────────────────────────────────────────────
// each renderer binds marks to their contributing record(s); `opts` (from the page)
// receives hover/click with a markInfo so it can show a provenance tooltip / drill in.
function infoRow(r, value, spec) {
  return { single: r.record_id, recordIds: [r.record_id], documentIds: [r.document_id],
           label: r.paper_title, value, n: 1, figure: spec };
}
function infoGroup(recIds, docIds, labl, value, spec) {
  return { single: recIds.length === 1 ? recIds[0] : null, recordIds: recIds,
           documentIds: docIds, label: labl, value, n: recIds.length, figure: spec };
}
function wireMark(sel, opts, infoFn) {
  if (!opts || (!opts.onHover && !opts.onSelect)) return sel;
  sel.style("cursor", opts.onSelect ? "pointer" : "default")
    .on("mouseover", (e, d) => opts.onHover && opts.onHover(infoFn(d), e))
    .on("mousemove", (e, d) => opts.onHover && opts.onHover(infoFn(d), e))
    .on("mouseleave", () => opts.onOut && opts.onOut())
    .on("click", (e, d) => opts.onSelect && opts.onSelect(infoFn(d)));
  return sel;
}

// ── dispatch ─────────────────────────────────────────────────────────────────
const RENDERERS = { bar, grouped_bar, stacked_bar, line, scatter, histogram, forest };

export function renderFigure(container, spec, rows, opts) {
  const sec = document.createElement("section");
  sec.className = "dash-fig" + (spec.data_sufficiency === "insufficient" ? " insufficient" : "");
  sec.innerHTML = `<div class="dash-fig-h"><span>${esc(spec.title || "Untitled figure")}</span>`
    + (opts && opts.onData ? `<button class="fig-data-btn" title="show the data behind this figure">⊞ Data</button>` : "")
    + `</div>`
    + (spec.question ? `<div class="dash-fig-q">${esc(spec.question)}</div>` : "");
  const plot = document.createElement("div");
  sec.appendChild(plot);
  container.appendChild(sec);
  if (opts && opts.onData) { const b = sec.querySelector(".fig-data-btn"); if (b) b.onclick = () => opts.onData(spec); }
  const fn = RENDERERS[spec.chart_kind];
  let n = rows.length;
  try {
    if (!fn) plot.innerHTML = `<p class="nodata">“${esc(spec.chart_kind || "?")}” charts aren’t supported yet.</p>`;
    else n = fn(plot, spec, rows, opts);
  } catch (e) { plot.innerHTML = `<p class="nodata">couldn’t render: ${esc(e.message)}</p>`; }
  const cap = document.createElement("div");
  cap.className = "dash-fig-n";
  const nn = n == null ? rows.length : n;
  cap.textContent = `${nn} record${nn === 1 ? "" : "s"}`;
  sec.appendChild(cap);
  return sec;
}

export function renderDashboard(container, viz_config, rows, opts) {
  container.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "dash-grid";
  container.appendChild(grid);
  const figures = (viz_config && viz_config.figures) || [];
  if (!figures.length) { grid.innerHTML = '<p class="muted">No figures in this analysis yet.</p>'; return; }
  figures.forEach((spec) => renderFigure(grid, spec, rows, opts));
}

// ── shared drawing helpers ───────────────────────────────────────────────────
function nodata(root, msg) { root.innerHTML = `<p class="nodata">${esc(msg || "No matching data.")}</p>`; return 0; }
function svgEl(root, W, H) {
  return d3.select(root).append("svg").attr("class", "fig-svg")
    .attr("viewBox", `0 0 ${W} ${H}`).attr("preserveAspectRatio", "xMinYMin meet");
}
function legend(root, cats) {
  if (cats.length < 2) return;
  const html = cats.map((c, i) => `<span class="lg"><span class="sw" style="background:var(--series-${(i % 8) + 1})"></span>${esc(String(c))}</span>`).join("");
  const div = document.createElement("div"); div.className = "fig-legend"; div.innerHTML = html;
  root.appendChild(div);
}
function xTitle(svg, W, H, t) { if (t) svg.append("text").attr("class", "ax-title").attr("x", W / 2).attr("y", H - 4).attr("text-anchor", "middle").text(t); }
function yTitle(svg, H, t) { if (t) svg.append("text").attr("class", "ax-title").attr("transform", "rotate(-90)").attr("x", -H / 2).attr("y", 12).attr("text-anchor", "middle").text(t); }

// ── bar (horizontal): y = category, x = value (count, or agg of x-var) ────────
function bar(root, spec, rows, opts) {
  const enc = spec.encodings || {};
  const catVar = (enc.y && enc.y.var) || (enc.x && enc.x.var);
  const valVar = enc.x && enc.x.var;
  const agg = (spec.transform && spec.transform.aggregate) || "count";
  if (!catVar) return nodata(root);
  let data = aggregateSeries(rows, catVar, agg === "count" ? null : valVar, agg);
  data = data.filter((d) => Number.isFinite(d.value));
  if (!data.length) {
    return nodata(root, agg === "count" ? "No matching data."
      : `No numeric values for “${valVar || catVar}” — switch Aggregate to Count, or pick a numeric field.`);
  }
  const W = 520, rowH = 26, m = { top: 6, right: 52, bottom: 30, left: 132 };
  const H = data.length * rowH + m.top + m.bottom;
  const svg = svgEl(root, W, H);
  const lo = Math.min(0, d3.min(data, (d) => d.value)), hi = Math.max(0, d3.max(data, (d) => d.value));
  const x = d3.scaleLinear().domain([lo, hi]).nice().range([m.left, W - m.right]);
  const y = d3.scaleBand().domain(data.map((d) => d.key)).range([m.top, H - m.bottom]).padding(0.22);
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(d3.axisBottom(x).ticks(5));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).tickSize(0))
    .call((g) => g.selectAll(".tick text").each(trunc(18)));
  const x0 = x(0);
  const bars = svg.append("g").selectAll("rect").data(data).join("rect").attr("class", "mark")
    .attr("x", (d) => Math.min(x0, x(d.value))).attr("y", (d) => y(d.key))
    .attr("width", (d) => Math.abs(x(d.value) - x0)).attr("height", y.bandwidth());
  bars.append("title").text((d) => `${d.key}: ${fmt(d.value)} (n=${d.n})`);
  wireMark(bars, opts, (d) => infoGroup(d.record_ids, d.document_ids, d.key, d.value, spec));
  svg.append("g").selectAll("text").data(data).join("text")
    .attr("x", (d) => x(d.value) + (d.value >= 0 ? 4 : -4)).attr("y", (d) => y(d.key) + y.bandwidth() / 2)
    .attr("dy", "0.35em").attr("text-anchor", (d) => d.value >= 0 ? "start" : "end").text((d) => fmt(d.value));
  return d3.sum(data, (d) => d.n);
}

// grouped / stacked bar: x = category, color = series, value = agg of y-var (or count)
function groupedStacked(root, spec, rows, stacked, opts) {
  const enc = spec.encodings || {};
  const catVar = (enc.x && enc.x.var) || (enc.y && enc.y.var);
  const colVar = enc.color && enc.color.var;
  const valVar = enc.y && enc.y.var && enc.x && enc.x.var ? enc.y.var : null;
  const agg = (spec.transform && spec.transform.aggregate) || "count";
  if (!catVar || !colVar) return nodata(root);
  const cats = Array.from(new Set(rows.map((r) => label(valueOf(r, catVar)))));
  const series = Array.from(new Set(rows.map((r) => label(valueOf(r, colVar)))));
  const grp = {};                                   // grp[cat][series] = member rows
  cats.forEach((c) => { grp[c] = {}; series.forEach((s) => { grp[c][s] = []; }); });
  rows.forEach((r) => { const c = label(valueOf(r, catVar)), s = label(valueOf(r, colVar));
    if (grp[c] && grp[c][s]) grp[c][s].push(r); });
  const mem = (c, s) => ({ recIds: grp[c][s].map((r) => r.record_id), docIds: grp[c][s].map((r) => r.document_id) });
  const rowsData = cats.map((c) => { const o = { cat: c }; series.forEach((s) => (o[s] = reduce(grp[c][s], agg === "count" ? null : valVar, agg) ?? 0)); return o; });
  const W = 560, m = { top: 8, right: 12, bottom: 46, left: 44 }, H = 300;
  const svg = svgEl(root, W, H);
  const x = d3.scaleBand().domain(cats).range([m.left, W - m.right]).padding(0.2);
  let y, layers;
  if (stacked) {
    layers = d3.stack().keys(series)(rowsData);
    y = d3.scaleLinear().domain([0, d3.max(layers[layers.length - 1] || [[0, 0]], (d) => d[1]) || 1]).nice().range([H - m.bottom, m.top]);
  } else {
    y = d3.scaleLinear().domain([0, d3.max(rowsData, (r) => d3.max(series, (s) => r[s])) || 1]).nice().range([H - m.bottom, m.top]);
  }
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(d3.axisBottom(x)).call((g) => g.selectAll(".tick text").each(trunc(10)).attr("transform", "rotate(-30)").style("text-anchor", "end"));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5));
  if (stacked) {
    svg.append("g").selectAll("g").data(layers).join("g").attr("class", (d, i) => seriesCls(i))
      .each(function (layer) {
        const key = layer.key;
        const rects = d3.select(this).selectAll("rect").data(layer).join("rect")
          .attr("x", (d) => x(d.data.cat)).attr("width", x.bandwidth())
          .attr("y", (d) => y(d[1])).attr("height", (d) => Math.max(0, y(d[0]) - y(d[1])));
        wireMark(rects, opts, (d) => { const g2 = mem(d.data.cat, key); return infoGroup(g2.recIds, g2.docIds, `${d.data.cat} · ${key}`, d[1] - d[0], spec); });
      });
  } else {
    const xs = d3.scaleBand().domain(series).range([0, x.bandwidth()]).padding(0.06);
    const groups = svg.append("g").selectAll("g").data(rowsData).join("g").attr("transform", (r) => `translate(${x(r.cat)},0)`);
    const grects = groups.selectAll("rect").data((r) => series.map((s, i) => ({ s, i, v: r[s], cat: r.cat }))).join("rect")
      .attr("class", (d) => seriesCls(d.i)).attr("x", (d) => xs(d.s)).attr("width", xs.bandwidth())
      .attr("y", (d) => y(d.v)).attr("height", (d) => Math.max(0, y(0) - y(d.v)));
    grects.append("title").text((d) => `${d.s}: ${fmt(d.v)}`);
    wireMark(grects, opts, (d) => { const g2 = mem(d.cat, d.s); return infoGroup(g2.recIds, g2.docIds, `${d.cat} · ${d.s}`, d.v, spec); });
  }
  legend(root, series);
  return rows.length;
}
function grouped_bar(root, spec, rows, opts) { return groupedStacked(root, spec, rows, false, opts); }
function stacked_bar(root, spec, rows, opts) { return groupedStacked(root, spec, rows, true, opts); }

// line: x = quant/temporal, y = agg of y-var by x (per color series), sorted by x
function line(root, spec, rows, opts) {
  const enc = spec.encodings || {};
  const xv = enc.x && enc.x.var, yv = enc.y && enc.y.var, cv = enc.color && enc.color.var;
  const agg = (spec.transform && spec.transform.aggregate) || "mean";
  if (!xv) return nodata(root);
  const series = cv ? Array.from(new Set(rows.map((r) => label(valueOf(r, cv))))) : ["all"];
  const line_of = (s) => {
    const rs = cv ? rows.filter((r) => label(valueOf(r, cv)) === s) : rows;
    const byX = d3.group(rs, (r) => num(valueOf(r, xv)));
    return Array.from(byX, ([xx, g]) => ({ x: xx, y: reduce(g, agg === "count" ? null : yv, agg),
      record_ids: g.map((r) => r.record_id), document_ids: g.map((r) => r.document_id), label: `${xv} = ${xx}` }))
      .filter((p) => p.x != null && Number.isFinite(p.y)).sort((a, b) => a.x - b.x);
  };
  const lines = series.map(line_of).filter((l) => l.length);
  if (!lines.length) return nodata(root);
  const W = 560, m = { top: 10, right: 14, bottom: 34, left: 46 }, H = 300;
  const svg = svgEl(root, W, H);
  const allpts = lines.flat();
  const x = d3.scaleLinear().domain(d3.extent(allpts, (p) => p.x)).nice().range([m.left, W - m.right]);
  const y = d3.scaleLinear().domain(d3.extent(allpts, (p) => p.y)).nice().range([H - m.bottom, m.top]);
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(d3.axisBottom(x).ticks(6).tickFormat(d3.format("d")));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5));
  const gen = d3.line().x((p) => x(p.x)).y((p) => y(p.y));
  lines.forEach((l, i) => svg.append("path").attr("class", `mark-stroke ${strokeCls(i)}`).attr("d", gen(l)));
  // invisible hover points so each (x, series) is traceable
  lines.forEach((l) => {
    const pts = svg.append("g").selectAll("circle").data(l).join("circle")
      .attr("cx", (p) => x(p.x)).attr("cy", (p) => y(p.y)).attr("r", 5).attr("fill", "transparent");
    pts.append("title").text((p) => `${p.label}: ${fmt(p.y)} (n=${p.record_ids.length})`);
    wireMark(pts, opts, (p) => infoGroup(p.record_ids, p.document_ids, p.label, p.y, spec));
  });
  xTitle(svg, W, H, xv); if (cv) legend(root, series);
  return rows.length;
}

// scatter: x, y quant; color categorical
function scatter(root, spec, rows, opts) {
  const enc = spec.encodings || {};
  const xv = enc.x && enc.x.var, yv = enc.y && enc.y.var, cv = enc.color && enc.color.var;
  if (!xv || !yv) return nodata(root);
  const cats = cv ? Array.from(new Set(rows.map((r) => label(valueOf(r, cv))))) : [];
  const pts = rows.map((r) => ({ x: num(valueOf(r, xv)), y: num(valueOf(r, yv)),
    ci: cv ? Math.max(0, cats.indexOf(label(valueOf(r, cv)))) : 0, r })).filter((p) => p.x != null && p.y != null);
  if (!pts.length) return nodata(root);
  const W = 560, m = { top: 10, right: 14, bottom: 34, left: 46 }, H = 300;
  const svg = svgEl(root, W, H);
  const x = d3.scaleLinear().domain(d3.extent(pts, (p) => p.x)).nice().range([m.left, W - m.right]);
  const y = d3.scaleLinear().domain(d3.extent(pts, (p) => p.y)).nice().range([H - m.bottom, m.top]);
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(d3.axisBottom(x).ticks(6));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5));
  const dots = svg.append("g").selectAll("circle").data(pts).join("circle").attr("class", (d) => seriesCls(d.ci))
    .attr("cx", (d) => x(d.x)).attr("cy", (d) => y(d.y)).attr("r", 3.4).attr("fill-opacity", 0.7);
  dots.append("title").text((d) => `${d.r.paper_title || "record"}: (${fmt(d.x)}, ${fmt(d.y)})`);
  wireMark(dots, opts, (d) => infoRow(d.r, d.y, spec));
  xTitle(svg, W, H, xv); yTitle(svg, H, yv); if (cv) legend(root, cats);
  return pts.length;
}

// histogram: bin x
function histogram(root, spec, rows, opts) {
  const enc = spec.encodings || {};
  const xv = (enc.x && enc.x.var) || (enc.y && enc.y.var);
  if (!xv) return nodata(root);
  const items = rows.map((r) => ({ v: num(valueOf(r, xv)), r })).filter((d) => d.v != null);
  if (!items.length) return nodata(root);
  const maxbins = (spec.transform && spec.transform.bin && spec.transform.bin.maxbins) || 20;
  const W = 560, m = { top: 10, right: 14, bottom: 34, left: 42 }, H = 300;
  const svg = svgEl(root, W, H);
  const x = d3.scaleLinear().domain(d3.extent(items, (d) => d.v)).nice().range([m.left, W - m.right]);
  const bins = d3.bin().domain(x.domain()).thresholds(x.ticks(maxbins)).value((d) => d.v)(items);
  const y = d3.scaleLinear().domain([0, d3.max(bins, (b) => b.length) || 1]).nice().range([H - m.bottom, m.top]);
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(d3.axisBottom(x).ticks(6));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5));
  const rects = svg.append("g").selectAll("rect").data(bins).join("rect").attr("class", "mark")
    .attr("x", (b) => x(b.x0) + 1).attr("width", (b) => Math.max(0, x(b.x1) - x(b.x0) - 1))
    .attr("y", (b) => y(b.length)).attr("height", (b) => y(0) - y(b.length));
  rects.append("title").text((b) => `[${fmt(b.x0)}, ${fmt(b.x1)}): ${b.length}`);
  wireMark(rects, opts, (b) => infoGroup(b.map((d) => d.r.record_id), b.map((d) => d.r.document_id),
    `[${fmt(b.x0)}, ${fmt(b.x1)})`, b.length, spec));
  xTitle(svg, W, H, xv);
  return items.length;
}

// forest / CI: y = study label, x = effect, error = {lo,hi}; ref line at 0
function forest(root, spec, rows, opts) {
  const enc = spec.encodings || {};
  const yv = (enc.y && enc.y.var), xv = (enc.x && enc.x.var);
  const lo = enc.error && enc.error.lo, hi = enc.error && enc.error.hi;
  if (!xv) return nodata(root);
  const pts = rows.map((r, i) => ({
    label: yv ? label(valueOf(r, yv)) : `#${i + 1}`,
    x: num(valueOf(r, xv)), lo: lo ? num(valueOf(r, lo)) : null, hi: hi ? num(valueOf(r, hi)) : null, r,
  })).filter((p) => p.x != null);
  if (!pts.length) return nodata(root);
  const W = 560, rowH = 22, m = { top: 8, right: 20, bottom: 30, left: 150 };
  const H = pts.length * rowH + m.top + m.bottom;
  const svg = svgEl(root, W, H);
  const xdomain = d3.extent(pts.flatMap((p) => [p.x, p.lo, p.hi].filter((v) => v != null)));
  if (xdomain[0] > 0) xdomain[0] = 0; if (xdomain[1] < 0) xdomain[1] = 0;
  const x = d3.scaleLinear().domain(xdomain).nice().range([m.left, W - m.right]);
  const y = d3.scaleBand().domain(pts.map((p) => p.label)).range([m.top, H - m.bottom]).padding(0.3);
  svg.append("g").attr("class", "ax").attr("transform", `translate(0,${H - m.bottom})`).call(d3.axisBottom(x).ticks(6));
  svg.append("g").attr("class", "ax").attr("transform", `translate(${m.left},0)`).call(d3.axisLeft(y).tickSize(0)).call((g) => g.selectAll(".tick text").each(trunc(22)));
  svg.append("line").attr("class", "forest-ref").attr("x1", x(0)).attr("x2", x(0)).attr("y1", m.top).attr("y2", H - m.bottom);
  const g = svg.append("g").selectAll("g").data(pts).join("g").attr("transform", (p) => `translate(0,${y(p.label) + y.bandwidth() / 2})`);
  g.filter((p) => p.lo != null && p.hi != null).append("line").attr("class", "forest-line")
    .attr("x1", (p) => x(p.lo)).attr("x2", (p) => x(p.hi)).attr("y1", 0).attr("y2", 0);
  g.append("circle").attr("class", "forest-dot").attr("cx", (p) => x(p.x)).attr("cy", 0).attr("r", 3.6)
    .append("title").text((p) => `${p.label}: ${fmt(p.x)}${p.lo != null ? ` [${fmt(p.lo)}, ${fmt(p.hi)}]` : ""}`);
  wireMark(g, opts, (p) => infoRow(p.r, p.x, spec));
  return pts.length;
}

// truncate long axis tick labels + keep the full text as a tooltip
function trunc(nchars) {
  return function () {
    const t = d3.select(this), full = t.text();
    if (full.length > nchars) { t.text(full.slice(0, nchars - 1) + "…"); t.append("title").text(full); }
  };
}

export { valueOf, num, aggregateSeries };
