// A workflow as a flow diagram: one box per step, an arrow for every dependency a step declares
// (a pass's or compute's `reads`, a compute's `edits`, a check's or review's `over`, the release's
// `gate`), laid out left to right by depth. Pure functions over the step list the panel or the
// card hands over, rendered as inline SVG that scales with its container.

const KIND_ICON = { extract: "⬚", agent: "⬚", check: "✓", derive: "Σ", vocabulary: "🏷", pages: "📖", crosscheck: "↔", loop: "↻", review: "👁", release: "⛁", pass: "⟶", compute: "ƒ" };
const BOX_W = 164, BOX_H = 40, COL_GAP = 46, ROW_GAP = 12, PAD = 8;

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

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

// the layers: a step's column is one past the deepest step it depends on; steps that depend on
// nothing sit in the first column, in the order they are declared
export function flowLayout(steps) {
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
  const rows = Math.max(1, ...cols.map((c) => c.length));
  const h = PAD * 2 + rows * BOX_H + (rows - 1) * ROW_GAP;
  const w = PAD * 2 + cols.length * BOX_W + Math.max(0, cols.length - 1) * COL_GAP;
  const pos = {};
  cols.forEach((col, ci) => {
    const used = col.length * BOX_H + (col.length - 1) * ROW_GAP;
    const top = PAD + (h - PAD * 2 - used) / 2;
    col.forEach((id, ri) => { pos[id] = { x: PAD + ci * (BOX_W + COL_GAP), y: top + ri * (BOX_H + ROW_GAP) }; });
  });
  return { cols, pos, edges, w, h };
}

// the SVG: boxes coloured by kind, a dot for the step's state, the step a run is on outlined;
// opts.current names that step, opts.href(step) may give each box a link
export function flowSvg(steps, opts = {}) {
  steps = (steps || []).filter((s) => s && s.id);
  if (!steps.length) return "";
  const { pos, edges, w, h } = flowLayout(steps);
  const byId = Object.fromEntries(steps.map((s) => [s.id, s]));
  const path = (e) => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return "";
    const x1 = a.x + BOX_W, y1 = a.y + BOX_H / 2, x2 = b.x, y2 = b.y + BOX_H / 2;
    const dx = Math.max(18, (x2 - x1) / 2);
    const dd = x2 > x1 ? `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}` : `M${x1},${y1} C${x1 + 30},${y1 - 24} ${x2 - 30},${y2 - 24} ${x2},${y2}`;
    return `<path class="flow-edge flow-edge-${esc(e.kind)}" d="${dd}" marker-end="url(#flow-arrow)"><title>${esc(byId[e.to].label || e.to)} ${e.kind === "gate" ? "waits for" : e.kind} ${esc(byId[e.from].label || e.from)}</title></path>`;
  };
  const box = (s) => {
    const p = pos[s.id];
    const label = String(s.label || s.id);
    const short = label.length > 22 ? label.slice(0, 21).trimEnd() + "…" : label;
    const extra = s.kind === "pass" && (s.k || 1) > 1 ? ` ×${s.k}` : s.kind === "review" && s.review && s.review.coders ? ` · ${s.review.coders} coder${s.review.coders === 1 ? "" : "s"}` : "";
    const tip = [label, s.kind + (s.k > 1 ? `, ${s.k} readings` : ""), s.state ? `state: ${s.state}` : "", s.help || ""].filter(Boolean).join("\n");
    const inner = `<rect class="flow-box flow-kind-${esc(s.kind)}${s.id === opts.current ? " flow-current" : ""}" x="${p.x}" y="${p.y}" width="${BOX_W}" height="${BOX_H}" rx="7"/>
      <text class="flow-icon" x="${p.x + 10}" y="${p.y + 25}">${esc(KIND_ICON[s.kind] || "·")}</text>
      <text class="flow-label" x="${p.x + 30}" y="${p.y + 17}">${esc(short)}</text>
      <text class="flow-sub" x="${p.x + 30}" y="${p.y + 31}">${esc(s.kind + extra)}</text>
      ${s.state ? `<circle class="flow-state flow-state-${esc(s.state)}" cx="${p.x + BOX_W - 10}" cy="${p.y + 10}" r="4"/>` : ""}
      <title>${esc(tip)}</title>`;
    const href = opts.href ? opts.href(s) : null;
    return href ? `<a href="${esc(href)}">${inner}</a>` : `<g>${inner}</g>`;
  };
  return `<svg class="flow" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="the workflow's steps and what each depends on">
    <defs><marker id="flow-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" class="flow-arrowhead"/></marker></defs>
    ${edges.map(path).join("")}${steps.map(box).join("")}</svg>`;
}
