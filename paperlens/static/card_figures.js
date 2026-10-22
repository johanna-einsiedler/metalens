// Figures a preset card can show, drawn in HTML and inline SVG so they follow the site's theme
// (fonts, colours) and its width. Card markdown names one with a fence:
//
//   ```figure claim-chain
//   One claim followed from the abstract to the corpus graph.   ← the accessible description
//   ```
//
// grammar.js renders that as <div class="md-figure" data-figure="claim-chain">; mountFigures fills
// it. Only the figures registered below exist: card markdown never carries HTML of its own.

const sub = (t) => `<sub>${t}</sub>`;
const m = (i) => `<i>m</i>${sub(i)}`;

// one node of a small graph: a rounded box with a monospace label, sized by its characters
function node(x, y, label) {
  const w = Math.round(label.length * 7.25 + 16);
  return { x, y, w, h: 24, label, cx: x + w / 2, cy: y + 12, right: x + w, left: x };
}
function boxes(...ns) {
  return ns.map((n) => `<g class="cf-node"><rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" rx="5"/>`
    + `<text x="${n.cx}" y="${n.cy + 4}" text-anchor="middle">${n.label}</text></g>`).join("");
}
// an arrow from the right edge of a to the left edge of b, with its label at the middle
function edge(a, b, cls, label, { dy = -8, anchor = "middle", at = 0.5 } = {}) {
  const x1 = a.right, y1 = a.cy, x2 = b.left - 2, y2 = b.cy;
  const lx = x1 + (x2 - x1) * at, ly = y1 + (y2 - y1) * at + dy;
  return `<line class="cf-edge ${cls}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" marker-end="url(#cf-arrow-${cls.split(" ")[0]})"/>`
    + `<text class="cf-elab ${cls.split(" ")[0]}" x="${lx}" y="${ly}" text-anchor="${anchor}">${label}</text>`;
}
const markers = `<defs>
  <marker id="cf-arrow-neg" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="cf-mk-neg"/></marker>
  <marker id="cf-arrow-mute" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="cf-mk-mute"/></marker></defs>`;
// a graph is drawn at its own size and only shrinks below it (inside a scrolling box on a phone)
const svg = (w, h, label, body) => `<svg class="cf-graph" viewBox="0 0 ${w} ${h}" style="max-width:${w}px" role="img" aria-label="${label}">${markers}${body}</svg>`;

function paperGraph() {
  const a = node(8, 10, "family.parental_death"), b = node(360, 10, "income.labor"), c = node(360, 54, "health.mental_health");
  return svg(530, 84, "Paper graph: parental death lowers labour income (causal) and is associated with worse mental health",
    `${edge(a, b, "neg", "− causal")}${edge(a, c, "neg dotted", "− associational", { dy: 20, at: 0.32, anchor: "end" })}${boxes(a, b, c)}`);
}
function corpusGraph() {
  const a = node(8, 10, "family.parental_death"), b = node(330, 10, "income.labor"), c = node(470, 56, "family.fertility");
  return svg(612, 86, "Corpus graph: two papers find that parental death lowers labour income; the effect of labour income on fertility is contradicted",
    `${edge(a, b, "neg thick", "− 2 papers: (−, −)")}${edge(b, c, "mute dashed", "± contradicted: (+, −)", { dy: 16, at: 0.25, anchor: "end" })}${boxes(a, b, c)}`);
}

const step = (n, title, body) => `<li class="cf-step"><div class="cf-h"><span class="cf-n">${n}</span>${title}</div><div class="cf-box">${body}</div></li>`;
const sentence = (quote, meta, to) => `<div class="cf-sent"><p class="cf-q">“${quote}”</p><p class="cf-meta">${meta}</p>${to ? `<p class="cf-to">${to}</p>` : ""}</div>`;
const estimate = (label, id, where, value, role, main) => `<div class="cf-group"><div class="cf-glab">${label}</div>`
  + `<div class="cf-est${main ? " cf-main" : ""}"><div class="cf-eid">${id}</div><div>${where}</div><div class="cf-num">${value}</div>`
  + `<div><span class="cf-role${main ? " main" : ""}">${role}</span> <i>κ</i> = 1</div></div></div>`;

function claimChain() {
  return `<ol class="cf-steps">
    ${step(1, "Claim", `<p class="cf-abs"><span class="cf-k">Abstract</span> “<em>Children who lose a parent suffer an earnings loss of about 10 percent as adults.</em>”</p>
      <p class="cf-math"><i>c</i> = (<i>x</i>, <i>y</i>, <i>s</i>) = (parental death, earnings loss, +): parental death raises the earnings loss.</p>`)}
    ${step(2, "Evidence", `<div class="cf-two">
      ${sentence("Losing a parent before age 18 lowers earnings at age 30 by 10 percent.", "introduction, p. 3 · quotes a number", `links to ${m(1)}`)}
      ${sentence("The loss is larger for daughters than for sons.", "introduction, p. 4 · no number, no table or figure named", `links to ${m(2)} and ${m(3)}`)}</div>`)}
    ${step(3, "Estimates", `<div class="cf-ests">
      ${estimate("all children at 30", m(1), "Table 2, col. 1", "−0.10 (0.02)", "main", true)}
      ${estimate("daughters", m(2), "Table 4, col. 2", "−0.13 (0.03)", "heterogeneity", false)}
      ${estimate("sons", m(3), "Table 4, col. 3", "−0.08 (0.03)", "heterogeneity", false)}</div>`)}
    ${step(4, "Scope", `<div class="cf-two">
      ${sentence("The sample consists of all children who lost a parent before age 18, born 1962–1983; earnings are measured at age 30.", `notes to Table 2, p. 12 · the sample restrictions of ${m(1)}`)}
      ${sentence("We follow the children in the registers from 1980 to 2013.", `data section, p. 8 · the time period of ${m(1)}, ${m(2)}, ${m(3)}; Table 4 heads the columns of ${m(2)}, ${m(3)} “daughters” and “sons”`)}</div>
      <p class="cf-line"><i>σ</i>(<i>c</i>) = (unit <code>individuals</code>, life stage <code>children</code>, event <code>parental_death</code> ← notes to Table 2; sex <code>all</code> ← the design record of ${m(1)}; comparison <code>daughters_vs_sons</code> ← the columns of ${m(2)}, ${m(3)}; period 1980–2013 ← the data section)</p>`)}
    ${step(5, "Design basis", `<div class="cf-scroll"><table class="cf-table"><tbody>
      <tr><td>${m(1)}</td><td>Table 2, col. 1</td><td>event study</td><td>𝒟${sub(1)} identified</td><td>main</td><td><span class="cf-badge">causal</span></td></tr>
      <tr><td>${m(2)}</td><td>Table 4, col. 2</td><td>difference in means, daughters</td><td>𝒟${sub(3)} descriptive</td><td>heterogeneity</td><td></td></tr>
      <tr><td>${m(3)}</td><td>Table 4, col. 3</td><td>difference in means, sons</td><td>𝒟${sub(3)} descriptive</td><td>heterogeneity</td><td></td></tr></tbody></table></div>`)}
    ${step(6, "Concepts", `<p class="cf-line">“parental death” ↦ (<code>family.parental_death</code>, <i>ρ</i>${sub("x")} = +1)</p>
      <p class="cf-line">“earnings loss” ↦ (<code>income.labor</code>, <i>ρ</i>${sub("y")} = −1)</p>
      <p class="cf-line"><i>s</i> · <i>ρ</i>${sub("x")} · <i>ρ</i>${sub("y")} = + · (+1) · (−1) = −</p>`)}
    ${step(7, "Paper graph", `<div class="cf-scroll">${paperGraph()}</div>
      <p class="cf-line"><i>e</i>(<i>c</i>) = (<code>family.parental_death</code>, <code>income.labor</code>, −)</p>`)}
    ${step(8, "Corpus graph", `<div class="cf-scroll">${corpusGraph()}</div>
      <p class="cf-line"><i>G</i> = <span class="cf-cup">⋃</span>${sub("<i>p</i>")} <i>G</i>${sub("<i>p</i>")}</p>`)}
  </ol>`;
}

// The review panel on one real page: the card with the model's values on the left, the page of
// the PDF with the located quotes on the right; the selected value and its highlight share the
// accent. Grydeland et al. (2012), BMC Public Health 12:1053 — CC BY 2.0 — page 6, Table 2; the
// values are the model's unverified extraction of that paper from the evaluation.
const RP_PAGE = "/static/cards/grydeland2012-p6-table2.jpg";   // Table 2, cropped from page 6 so it stays readable
const RP_HL = {   // [left, top, width, height] in % of the crop, from the rects the locator placed on the page
  es143: [[2.35, 58.22, 16.21, 3.25], [27.06, 58.22, 2.5, 3.25]],
  es099: [[2.35, 69.56, 10.13, 3.25], [27.06, 69.56, 2.5, 3.25]],
  n:     [[2.35, 2.24, 44.33, 3.63]],
};
const hl = (boxes, cls) => boxes.map(([x, y, w, h]) => `<span class="rp-hl ${cls}" style="left:${x}%;top:${y}%;width:${w}%;height:${h}%"></span>`).join("");
const cell = (v, cls = "") => `<td class="${cls}">${v}</td>`;
function reviewPanelDirect() {
  return `<div class="rp" role="group" aria-label="The review panel: the extracted values of one sample next to the page they were read from">
    <div class="rp-card">
      <div class="rp-title">HEIA study: Norwegian 11-year-old adolescents <span class="rp-status">unverified</span><span class="rp-tool">↻ history</span></div>
      <table class="rp-meta"><tbody>
        <tr><th>Country</th><td>Norway</td><th>Year</th><td>2012</td></tr>
        <tr><th>% female</th><td class="rp-cite" title="p. 5 · “Girls (% (n)) 50 (555)”">50 <small>p. 5</small></td><th>Mean age</th><td class="rp-cite" title="p. 5 · “Age (years) 11.2 (0.3)”">11.2 <small>p. 5</small></td></tr>
      </tbody></table>
      <div class="rp-k">Effect sizes <span class="rp-conf">high</span></div>
      <table class="rp-rows"><thead><tr><th>var1</th><th>var2</th><th>es</th><th>type</th><th>n</th></tr></thead><tbody>
        <tr>${cell("bm")}${cell("pa")}${cell("0.99 <small>p. 6</small>", "rp-cite")}${cell("or")}${cell("1103 <small>p. 6</small>", "rp-cite")}</tr>
        <tr class="rp-selrow">${cell("bm")}${cell("vg")}${cell("1.43 <small>p. 6</small>", "rp-cite rp-sel")}${cell("or")}${cell("1103 <small>p. 6</small>", "rp-cite")}</tr>
      </tbody></table>
      <p class="rp-note">bm = overweight/obese (IOTF cut-offs, measured BMI) · vg = hours per day of computer or electronic games · pa = accelerometer-measured MVPA</p>
      <div class="rp-foot"><span class="muted">Checked every field?</span><span class="rp-btn">✓ verify</span><span class="rp-btn">⚑ flag</span></div>
    </div>
    <div class="rp-page"><span class="rp-pg">p. 6 · Table 2</span>
      <img src="${RP_PAGE}" alt="Table 2 of Grydeland et al. (2012), page 6: factors associated with being overweight or obese" loading="lazy">
      ${hl(RP_HL.es143, "rp-hl-sel")}${hl(RP_HL.es099, "")}${hl(RP_HL.n, "")}
    </div>
  </div>`;
}

const FIGURES = { "claim-chain": claimChain, "review-panel-direct": reviewPanelDirect };

export function mountFigures(root) {
  root.querySelectorAll(".md-figure[data-figure]").forEach((el) => {
    const make = FIGURES[el.dataset.figure];
    if (!make) { el.innerHTML = `<p class="muted">(figure “${el.dataset.figure.replace(/[<>&"]/g, "")}” is not available)</p>`; return; }
    el.innerHTML = `<figure class="cf" aria-label="${(el.dataset.alt || "").replace(/"/g, "&quot;")}">${make()}</figure>`;
  });
}
