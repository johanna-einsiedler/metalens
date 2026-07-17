// Landing stat band — computed live from the public datasets.
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";

const stat = (num, lbl) =>
  `<div class="stat"><div class="num">${num}</div><div class="lbl">${esc(lbl)}</div></div>`;

async function init() {
  const el = document.getElementById("stats");
  if (!el) return;
  let ds = [];
  try { ds = (await api.datasetsPublic()).datasets || []; } catch { /* leave empty */ }
  const records = ds.reduce((a, d) => a + (d.credibility.n_records || 0), 0);
  const audited = ds.reduce((a, d) => a + (d.credibility.audited || 0), 0);
  const pct = records ? Math.round((100 * audited) / records) : 0;
  el.innerHTML =
    stat(ds.length, "datasets")
    + stat(records.toLocaleString(), "records, all click-to-source")
    + stat(`${pct}%`, "human-verified");
}

init();
