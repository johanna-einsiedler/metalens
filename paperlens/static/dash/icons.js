// Stylised template icons for the dashboard composer. An icon is a small sketch of the chart
// with its axes captioned by the columns bound to them, so a proposed block can be read at a
// glance: "a forest plot of Hedges' g, one line per study". Colour only via theme classes.
import { esc } from "/static/grammar.js";

const W = 150, H = 96, L = 22, B = 18, T = 6, R = 6;          // plot area inside the caption gutters
const cut = (s, n) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");
const axes = `<path class="ic-ax" d="M${L} ${T} V${H - B} H${W - R}"/>`;
const frame = (inner, { x, y } = {}) =>
  `<svg class="tpl-icon" viewBox="0 0 ${W} ${H}" role="img" aria-hidden="true">${inner}`
  + (x !== undefined ? `<text class="ic-cap${x ? "" : " ic-unbound"}" x="${y === undefined ? W / 2 : (L + W - R) / 2}" y="${H - 4}" text-anchor="middle">${esc(cut(x, y === undefined ? 30 : 24) || "choose…")}</text>` : "")
  + (y !== undefined ? `<text class="ic-cap${y ? "" : " ic-unbound"}" transform="rotate(-90)" x="${-(T + H - B) / 2}" y="9" text-anchor="middle">${esc(cut(y, 14) || "choose…")}</text>` : "")
  + `</svg>`;

const SKETCH = {
  forest: () => axes + `<line class="ic-ref" x1="70" x2="70" y1="${T}" y2="${H - B}"/>`
    + [[52, 96, 78, 16], [60, 110, 88, 30], [40, 92, 64, 44], [66, 120, 94, 58], [58, 84, 72, 72]].map(([a, b, c, yy]) =>
      `<line class="ic-line" x1="${a}" x2="${b}" y1="${yy}" y2="${yy}"/><rect class="ic-mark" x="${c - 3}" y="${yy - 3}" width="6" height="6"/>`).join(""),
  scatter: () => axes + [[40, 60], [52, 50], [60, 56], [70, 40], [82, 44], [92, 30], [104, 34], [118, 18], [64, 66], [100, 22]].map(([x, y], i) =>
    `<circle class="${i % 3 ? "ic-mark" : "ic-mark-2"}" cx="${x}" cy="${y}" r="3.2"/>`).join(""),
  dot_strip: () => axes + [16, 32, 48, 64].map((yy, k) => `<line class="ic-grid" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/>`
    + [38 + k * 9, 58 + k * 5, 76 - k * 3, 98 + k * 4].map((x) => `<circle class="ic-mark" cx="${x}" cy="${yy}" r="2.8"/>`).join("")).join(""),
  bar: () => axes + [[14, 96], [30, 70], [46, 52], [62, 30]].map(([yy, w]) => `<rect class="ic-mark" x="${L + 1}" y="${yy - 5}" width="${w}" height="10"/>`).join(""),
  histogram: () => axes + [10, 24, 40, 56, 44, 26, 12].map((h, k) => `<rect class="ic-mark" x="${L + 6 + k * 16}" y="${H - B - h}" width="14" height="${h}"/>`).join(""),
  heatmap: () => Array.from({ length: 4 }, (_, r) => Array.from({ length: 6 }, (_, c) =>
    `<rect class="ic-mark" style="fill-opacity:${(0.15 + ((r * 7 + c * 5) % 9) / 10).toFixed(2)}" x="${L + 4 + c * 20}" y="${T + 2 + r * 17}" width="18" height="15"/>`).join("")).join(""),
  summary_table: () => `<rect class="ic-head" x="${L}" y="${T + 2}" width="${W - L - R}" height="12"/>`
    + [24, 38, 52, 66].map((yy) => `<line class="ic-grid" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/>`
      + [30, 70, 100, 124].map((x, k) => `<rect class="ic-cell" x="${x}" y="${yy + 4}" width="${k ? 16 : 30}" height="5"/>`).join("")).join(""),
  rows_table: () => `<rect class="ic-head" x="${L}" y="${T + 2}" width="${W - L - R}" height="12"/>`
    + [24, 36, 48, 60, 72].map((yy) => `<line class="ic-grid" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/>`
      + [28, 62, 92, 120].map((x, k) => `<rect class="ic-cell" x="${x}" y="${yy + 4}" width="${[26, 22, 18, 14][k]}" height="4"/>`).join("")).join(""),
  stat: () => `<text class="ic-big" x="${W / 2}" y="54" text-anchor="middle">#</text><rect class="ic-cell" x="${W / 2 - 26}" y="66" width="52" height="5"/>`,
};

// labels: {x, y} = the captions of the two axes ("" → the dashed "choose…" placeholder, undefined → no caption)
export function iconSvg(iconId, labels) { return frame((SKETCH[iconId] || SKETCH.rows_table)(), labels || {}); }

// which bound column captions which axis of a template's icon
export function iconLabels(tpl, block, colLabel) {
  const b = block.bindings || {}, name = (slot) => (b[slot] && (b[slot].column || (b[slot].columns || [])[0])) || "";
  const lab = (slot) => { const n = name(slot); const agg = b[slot] && b[slot].agg; return n ? `${agg ? agg + " of " : ""}${colLabel(n)}` : ""; };
  switch (tpl.id) {
    case "forest": return { x: lab("x"), y: lab("label") || "study" };
    case "scatter": return { x: lab("x"), y: lab("y") };
    case "dot_strip": return { x: lab("x"), y: lab("y") };
    case "bar": return { x: name("x") ? lab("x") : "number of rows", y: lab("y") };
    case "histogram": return { x: lab("x"), y: "rows" };
    case "heatmap": return { x: lab("x"), y: lab("y") };
    case "summary_table": return { x: `${lab("value") || "choose…"} by ${lab("group") || "…"}` };
    case "rows_table": return { x: "the rows, every cell traceable" };
    default: return { x: lab("value") || lab("column") || "rows" };
  }
}
