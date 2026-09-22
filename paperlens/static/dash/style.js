// The look of ONE dashboard: a vibe (palette + surfaces, defined in theme.css), a typeface and,
// if the owner wants, their own colours. Stored as spec.theme = {vibe, font, colors{mark, series[]}}.
// Charts take every colour from CSS custom properties, so a change shows at once, without a redraw.
import { esc } from "/static/grammar.js";

const SERIES_N = 4;                                         // own colours for the slots a colour legend can use
const VAR = (k) => (k === "mark" ? "--mark" : `--series-${k + 1}`);

export function applyTheme(theme) {
  const t = theme || {}, b = document.body, c = t.colors || {};
  if (t.vibe && t.vibe !== "clean") b.dataset.vibe = t.vibe; else delete b.dataset.vibe;
  if (t.font && t.font !== "sans") b.dataset.dashFont = t.font; else delete b.dataset.dashFont;
  ["mark", 0, 1, 2, 3, 4, 5, 6, 7].forEach((k) => b.style.removeProperty(VAR(k)));
  if (c.mark) b.style.setProperty("--mark", c.mark);
  (c.series || []).forEach((hex, i) => { if (hex) b.style.setProperty(VAR(i), hex); });
}

// how far apart two colours look (OKLab distance × 100, the measure the palette was checked with)
function oklab(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((u) => (u <= 0.04045 ? u / 12.92 : ((u + 0.055) / 1.055) ** 2.4));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b), m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b),
    s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}
const deltaE = (a, b) => { const x = oklab(a), y = oklab(b); return 100 * Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]); };
const tokenHex = (name) => { const v = getComputedStyle(document.body).getPropertyValue(name).trim(); return /^#[0-9a-f]{6}$/i.test(v) ? v.toLowerCase() : null; };

// host: an element under the dashboard head; registry: {vibes, fonts}; onSave(theme) → Promise
export function mountStylePanel(host, { registry, theme, onSave }) {
  const saved = JSON.parse(JSON.stringify(theme || {}));
  let cur = JSON.parse(JSON.stringify(saved)); cur.colors = cur.colors || {};
  const own = () => !!(cur.colors.mark || (cur.colors.series || []).some(Boolean));

  function draw() {
    const vibe = cur.vibe || "clean", font = cur.font || (vibe === "journal" ? "serif" : "sans");
    host.innerHTML = `<div class="ds-card dash-style"><div class="ds-card-h">Style</div>`
      + `<div class="st-row"><span class="st-l">Look</span><div class="st-vibes">${registry.vibes.map((v) =>
        `<button type="button" class="st-vibe${v.id === vibe ? " on" : ""}" data-vibe-id="${esc(v.id)}" title="${esc(v.description)}">`
        + `<span class="vibe-swatch" data-vibe="${esc(v.id)}"><i></i><i></i><i></i><i></i></span><b>${esc(v.label)}</b></button>`).join("")}</div></div>`
      + `<div class="st-row"><span class="st-l">Font</span><select id="st-font">${registry.fonts.map((f) => `<option value="${esc(f.id)}"${f.id === font ? " selected" : ""}>${esc(f.label)}</option>`).join("")}</select></div>`
      + `<div class="st-row"><span class="st-l">Colours</span><div class="st-colors">`
      + `<label class="st-c"><input type="color" data-c="mark"/> <span>marks of one series</span></label>`
      + Array.from({ length: SERIES_N }, (_, i) => `<label class="st-c"><input type="color" data-c="${i}"/> <span>group ${i + 1}</span></label>`).join("")
      + `<button type="button" class="btn btn-ghost btn-sm" id="st-reset"${own() ? "" : " disabled"}>Back to the look’s colours</button></div></div>`
      + `<p class="dash-warn" id="st-warn" hidden></p>`
      + `<div class="st-row st-end"><button type="button" class="btn btn-primary btn-sm" id="st-save">Save style</button>`
      + `<button type="button" class="btn btn-ghost btn-sm" id="st-cancel">Cancel</button><span class="muted" id="st-msg"></span></div></div>`;
    applyTheme(cur);
    host.querySelectorAll("input[type=color]").forEach((el) => {       // after applying: the pickers show what is on screen
      const k = el.dataset.c === "mark" ? "mark" : +el.dataset.c;
      el.value = tokenHex(VAR(k)) || el.value;
      el.oninput = () => {
        if (k === "mark") cur.colors.mark = el.value;
        else { cur.colors.series = cur.colors.series || []; for (let j = 0; j < k; j++) cur.colors.series[j] = cur.colors.series[j] || null; cur.colors.series[k] = el.value; }
        applyTheme(cur); warn(); host.querySelector("#st-reset").disabled = false;
      };
    });
    host.querySelectorAll("[data-vibe-id]").forEach((el) => (el.onclick = () => { cur.vibe = el.dataset.vibeId; draw(); }));
    host.querySelector("#st-font").onchange = (e) => { cur.font = e.target.value; applyTheme(cur); };
    host.querySelector("#st-reset").onclick = () => { cur.colors = {}; draw(); };
    host.querySelector("#st-cancel").onclick = () => { applyTheme(saved); host.innerHTML = ""; };
    host.querySelector("#st-save").onclick = async () => {
      const msg = host.querySelector("#st-msg"); msg.textContent = "Saving…";
      try { await onSave(cur); host.innerHTML = ""; } catch (e) { msg.textContent = `Could not save: ${e.message}`; }
    };
    warn();
  }
  // own colours are the owner's call, but two groups that look alike defeat the legend: say so
  function warn() {
    const hex = Array.from({ length: SERIES_N }, (_, i) => tokenHex(VAR(i))), close = [];
    for (let i = 0; i < hex.length; i++) for (let j = i + 1; j < hex.length; j++) if (hex[i] && hex[j] && deltaE(hex[i], hex[j]) < 15) close.push(`group ${i + 1} and group ${j + 1}`);
    const w = host.querySelector("#st-warn");
    w.hidden = !close.length || !own();
    w.textContent = close.length ? `Hard to tell apart: ${close.join(", ")}. Pick colours that differ more in hue or brightness.` : "";
  }
  draw();
}
