// A workflow as a flow diagram: one box per step, an arrow for every dependency a step declares
// (a pass's or compute's `reads`, a compute's `edits`, a check's or review's `over`, the release's
// `gate`), laid out left to right by depth. When the columns do not fit the width the caller
// gives, they go on in a second row of boxes underneath (and a third, …); an arrow into a later
// row leaves to the right, runs down the right margin, back along the gap above the target's row
// and down the gap before the target's column — the arrows out of one step share that trunk.
// Every arrow is straight segments joined at right angles. Pure functions over the step list the
// panel or the card hands over, rendered as inline SVG in the palette's own colours (base.css).

const KIND_ICON = { extract: "⬚", agent: "⬚", check: "✓", derive: "Σ", vocabulary: "🏷", pages: "📖", crosscheck: "↔", loop: "↻", review: "👁", release: "⛁", pass: "⟶", compute: "ƒ" };
const BOX_W = 156, BOX_H = 40, COL_GAP = 38, ROW_GAP = 12, PAD = 8;
const BAND_GAP = 26, LANE = 4;     // between two rows of boxes; the room each step with arrows into a later row takes

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
const r1 = (v) => Math.round(v * 10) / 10;

// how wide the diagram may be: the element's inner width, its padding taken off
export function flowWidth(el) {
  if (!el || typeof getComputedStyle !== "function") return 0;
  const cs = getComputedStyle(el);
  return Math.max(0, el.clientWidth - (parseFloat(cs.paddingLeft) || 0) - (parseFloat(cs.paddingRight) || 0));
}

// the arrows: [{from, to, kind}] — kind names the relation (reads | edits | over | gate)
export function flowEdges(steps) {
  const ids = new Set((steps || []).map((s) => s.id));
  const out = [];
  const add = (from, to, kind) => { if (ids.has(from) && ids.has(to) && from !== to) out.push({ from, to, kind }); };
  for (const s of steps || []) {
    for (const r of s.reads || []) add(r, s.id, "reads");
    if (s.edits) add(s.edits, s.id, "edits");
    const over = Array.isArray(s.over) ? s.over : s.over ? [s.over] : [];
    for (const o of over) add(o, s.id, "over");
    const gate = Array.isArray(s.gate_steps) ? s.gate_steps : Array.isArray(s.gate) ? s.gate : [];
    for (const g of gate) add(g, s.id, "gate");
  }
  return out;
}

// columns side by side: opts.maxCols, or as many as opts.width pixels hold; all of them otherwise
function perRow(opts, n) {
  if (opts.maxCols) return Math.max(1, Math.floor(opts.maxCols));
  if (opts.width) return Math.max(1, Math.floor((opts.width - PAD * 2 + COL_GAP) / (BOX_W + COL_GAP)));
  return Math.max(1, n);
}

// the layers: a step's column is one past the deepest step it depends on; steps that depend on
// nothing sit in the first column, in the order they are declared. The columns are dealt into
// rows of boxes ("bands") of as many columns as fit; `pos` gives each box its corner, band and
// row; `wraps` are the arrows into a later band and `lane` the trunk each of their sources gets
// (the margins they take may cost a column).
export function flowLayout(steps, opts = {}) {
  const edges = flowEdges(steps);
  const preds = {};
  for (const e of edges) (preds[e.to] = preds[e.to] || []).push(e.from);
  const depth = {};
  const seen = new Set();
  const d = (id) => {
    if (id in depth) return depth[id];
    if (seen.has(id)) return 0;                                     // a cycle never happens in a valid workflow; stop anyway
    seen.add(id);
    const ps = preds[id] || [];
    depth[id] = ps.length ? 1 + Math.max(...ps.map(d)) : 0;
    return depth[id];
  };
  const cols = [];
  for (const s of steps || []) {
    const c = d(s.id);
    (cols[c] = cols[c] || []).push(s.id);
  }
  for (let i = 0; i < cols.length; i++) cols[i] = cols[i] || [];
  let per = perRow(opts, cols.length), bands, wraps, sources, left, right, widest;
  for (;;) {
    bands = [];
    for (let i = 0; i < cols.length; i += per) bands.push(cols.slice(i, i + per));
    const bandOf = {};
    bands.forEach((band, bi) => band.forEach((col) => col.forEach((id) => { bandOf[id] = bi; })));
    wraps = edges.filter((e) => bandOf[e.to] > bandOf[e.from]);
    sources = [...new Set(wraps.map((e) => e.from))];
    left = wraps.length ? COL_GAP / 2 + 4 : 0;                      // room for the turn before a first column
    right = wraps.length ? 6 + sources.length * LANE : 0;           // the trunks down the right margin
    widest = Math.max(0, ...bands.map((b) => b.length));
    if (per <= 1 || !opts.width || PAD * 2 + left + right + widest * BOX_W + (widest - 1) * COL_GAP <= opts.width) break;
    per -= 1;
  }
  const gap = BAND_GAP + sources.length * LANE;
  const rightEdge = PAD + left + widest * BOX_W + Math.max(0, widest - 1) * COL_GAP;
  const w = rightEdge + right + PAD;
  const pos = {}, bandTop = [];
  let y = PAD;
  bands.forEach((band, bi) => {
    const rows = Math.max(1, ...band.map((c) => c.length));
    const bh = rows * BOX_H + (rows - 1) * ROW_GAP;
    bandTop.push(y);
    band.forEach((col, ci) => {
      const used = col.length * BOX_H + (col.length - 1) * ROW_GAP;
      const top = y + (bh - used) / 2;
      col.forEach((id, ri) => { pos[id] = { x: PAD + left + ci * (BOX_W + COL_GAP), y: top + ri * (BOX_H + ROW_GAP), band: bi, row: ri, rows: col.length }; });
    });
    y += bh + gap;
  });
  const h = (bands.length ? y - gap : y) + PAD;
  // the trunks, the lowest source innermost (and its run along a gap uppermost), so one never crosses another
  const lane = Object.fromEntries(sources.slice().sort((p, q) => (pos[q].y - pos[p].y) || (pos[q].x - pos[p].x)).map((id, k) => [id, k]));
  return { cols, bands, pos, edges, wraps, lane, w, h, left, right, gap, bandTop, rightEdge };
}

// the SVG: boxes coloured by kind, a dot for the step's state, the step a run is on outlined;
// opts.current names that step, opts.selected the one chosen to read about, opts.href(step) may
// give each box a link (else every box carries data-step for a click handler), opts.width
// (pixels) or opts.maxCols says how many columns fit side by side
export function flowSvg(steps, opts = {}) {
  steps = (steps || []).filter((s) => s && s.id);
  if (!steps.length) return "";
  const { pos, edges, lane, gap, bandTop, rightEdge, w, h } = flowLayout(steps, opts);
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
  const path = (e) => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return "";
    const x1 = a.x + BOX_W, y1 = a.y + BOX_H / 2, x2 = b.x, y2 = b.y + BOX_H / 2;
    // the turn before the target's column, one lane per target row so arrows into neighbouring boxes stay apart
    const xv = x2 - COL_GAP / 2 + (b.row - (b.rows - 1) / 2) * LANE;
    let dd;
    if (a.band === b.band) dd = y1 === y2 ? `M${r1(x1)},${r1(y1)} H${r1(x2)}` : `M${r1(x1)},${r1(y1)} H${r1(xv)} V${r1(y2)} H${r1(x2)}`;
    else {
      // a later row of boxes: out to the source's trunk in the right margin, down to its lane in
      // the gap above the target's row, back to the turn before the target's column, down, in
      const k = lane[e.from];
      const xr = rightEdge + 6 + k * LANE, yg = bandTop[b.band] - gap + 10 + k * LANE;
      dd = `M${r1(x1)},${r1(y1)} H${r1(xr)} V${r1(yg)} H${r1(xv)} V${r1(y2)} H${r1(x2)}`;
    }
    return `<path class="flow-edge flow-edge-${esc(e.kind)}" d="${dd}" marker-end="url(#flow-arrow)"><title>${esc(byId[e.to].label || e.to)} ${e.kind === "gate" ? "waits for" : e.kind} ${esc(byId[e.from].label || e.from)}</title></path>`;
  };
  const box = (s) => {
    const p = pos[s.id];
    const label = String(s.label || s.id);
    const short = label.length > 20 ? label.slice(0, 19).trimEnd() + "…" : label;
    const rv = s.kind === "review" ? (s.review || {}) : null;     // a review's purpose (audit, routed, …) and its coders
    const extra = s.kind === "pass" && (s.k || 1) > 1 ? ` ×${s.k}` : rv ? `${rv.purpose || s.purpose ? ` · ${rv.purpose || s.purpose}` : ""}${rv.coders ? ` · ${rv.coders} coder${rv.coders === 1 ? "" : "s"}` : ""}` : "";
    const tip = [label, s.kind + (s.k > 1 ? `, ${s.k} readings` : ""), s.state ? `state: ${s.state}` : "", s.help || ""].filter(Boolean).join("\n");
    const inner = `<rect class="flow-box flow-kind-${esc(s.kind)}${s.id === opts.current ? " flow-current" : ""}${s.id === opts.selected ? " flow-selected" : ""}" x="${r1(p.x)}" y="${r1(p.y)}" width="${BOX_W}" height="${BOX_H}" rx="7"/>
      <text class="flow-icon" x="${r1(p.x + 10)}" y="${r1(p.y + 25)}">${esc(KIND_ICON[s.kind] || "·")}</text>
      <text class="flow-label" x="${r1(p.x + 28)}" y="${r1(p.y + 17)}">${esc(short)}</text>
      <text class="flow-sub" x="${r1(p.x + 28)}" y="${r1(p.y + 31)}">${esc(s.kind + extra)}</text>
      ${s.state ? `<circle class="flow-state flow-state-${esc(s.state)}" cx="${r1(p.x + BOX_W - 10)}" cy="${r1(p.y + 10)}" r="4"/>` : ""}
      <title>${esc(tip)}</title>`;
    const href = opts.href ? opts.href(s) : null;
    return href ? `<a href="${esc(href)}">${inner}</a>` : `<g class="flow-node" data-step="${esc(s.id)}" tabindex="0" role="button" aria-label="${esc(label)}: show this step">${inner}</g>`;
  };
  return `<svg class="flow" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" preserveAspectRatio="xMinYMin meet" role="img" aria-label="the workflow's steps and what each depends on">
    <defs><marker id="flow-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" class="flow-arrowhead"/></marker></defs>
    ${edges.map(path).join("")}${steps.map(box).join("")}</svg>`;
}
