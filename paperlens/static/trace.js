// Figure traceability controller: turns dashboard mark events (from figures.js) into
// a provenance tooltip (hover), navigation to data review (click a single-record mark),
// and a "data behind this figure" table (aggregate drill-down / the ⊞ Data toggle).
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";
import { showTip, hideTip } from "/static/tooltip.js";

const recCache = new Map();
function fetchRecord(id) {
  if (!recCache.has(id)) recCache.set(id, api.record(id).catch(() => null));
  return recCache.get(id);
}
const figValueVar = (spec) => { const e = (spec && spec.encodings) || {}; return (e.x && e.x.var) || (e.y && e.y.var) || null; };
function deepGet(obj, path) {
  let cur = obj || {};
  for (const part of String(path).split(".")) { if (cur && typeof cur === "object" && part in cur) cur = cur[part]; else return undefined; }
  return cur;
}
function fmtCell(v) {
  if (v == null) return "";
  if (typeof v === "object") return JSON.stringify(v).slice(0, 40);
  return String(v);
}

export function createTrace(rows, panelHost, { navigate = true } = {}) {
  const byId = new Map(rows.map((r) => [r.record_id, r]));
  let hoverSeq = 0;

  function tooltipSingle(rd, info, e) {
    const p = rd.paper || {}, ev = (rd.evidence || [])[0] || {};
    const sub = [p.year, p.journal].filter(Boolean).join(" · ");
    const st = rd.record.verification_status;
    showTip(
      `<div class="tp-t">${esc(p.title || info.label || "record")}</div>`
      + (sub ? `<div class="tp-m">${esc(sub)}</div>` : "")
      + `<div class="tp-v">${esc(String(info.value))} · <span class="status ${esc(st)}">${esc(st)}</span></div>`
      + (ev.page ? `<div class="tp-e">p.${ev.page}${ev.source ? ` · ${esc(ev.source)}` : ""} — “${esc((ev.snippet || "").slice(0, 150))}”</div>` : "")
      + `<div class="tp-c">click to review →</div>`, e.clientX, e.clientY);
  }

  const opts = {
    onHover(info, e) {
      if (info.single) {
        const seq = ++hoverSeq;
        showTip(`<div class="tp-t">${esc(info.label || "record")}</div><div class="tp-m">loading provenance…</div>`, e.clientX, e.clientY);
        fetchRecord(info.single).then((rd) => { if (seq === hoverSeq && rd) tooltipSingle(rd, info, e); });
      } else {
        showTip(`<div class="tp-t">${esc(info.label || "")}</div>`
          + `<div class="tp-v">${esc(String(info.value))} · ${info.n} record${info.n === 1 ? "" : "s"}</div>`
          + `<div class="tp-c">click to see the ${info.n} contributing entries →</div>`, e.clientX, e.clientY);
      }
    },
    onOut() { hideTip(); },
    onSelect(info) {
      hideTip();
      if (info.single && navigate && info.documentIds[0]) {
        location.href = `/workspace?doc=${info.documentIds[0]}&rec=${info.single}`;
      } else {
        openPanel(info.recordIds, info.figure, info.label);
      }
    },
    onData(spec) { openPanel(rows.map((r) => r.record_id), spec, spec.title || "This figure"); },
  };

  async function openPanel(recordIds, spec, title) {
    if (!panelHost) return;
    const ids = [...new Set((recordIds || []).filter(Boolean))];
    const valVar = figValueVar(spec);
    panelHost.hidden = false;
    panelHost.innerHTML = `<div class="tp-head"><b>Data behind “${esc(title || "figure")}”</b>`
      + `<span class="muted"> · ${ids.length} record${ids.length === 1 ? "" : "s"}</span>`
      + `<button class="tp-x" title="close">✕</button></div><div class="tp-body">loading…</div>`;
    panelHost.querySelector(".tp-x").onclick = () => { panelHost.hidden = true; };
    panelHost.scrollIntoView({ behavior: "smooth", block: "nearest" });
    let prov = [];
    try { prov = (await api.recordsProvenance(ids)).records || []; } catch { /* best-effort */ }
    const pv = new Map(prov.map((p) => [p.record_id, p]));
    const body = ids.map((id) => {
      const r = byId.get(id) || {}, p = pv.get(id) || {};
      const val = valVar ? fmtCell(deepGet(r.field_values, valVar)) : "";
      const st = r.verification_status || p.verification_status || "";
      const doc = r.document_id || p.document_id;
      return `<tr>`
        + `<td>${esc(r.paper_title || p.paper_title || "—")}</td>`
        + `<td class="tp-num">${esc(val)}</td>`
        + `<td><span class="status ${esc(st)}">${esc(st || "—")}</span></td>`
        + `<td>${p.page ? "p." + p.page : "—"}</td>`
        + `<td class="tp-ev">${esc((p.snippet || "").slice(0, 120))}</td>`
        + `<td>${doc ? `<a href="/workspace?doc=${esc(doc)}&rec=${esc(id)}">▶ Review</a>` : ""}</td></tr>`;
    }).join("");
    panelHost.querySelector(".tp-body").innerHTML =
      `<div class="tp-scroll"><table class="tp-table"><thead><tr>`
      + `<th>Paper</th><th>${esc(valVar || "value")}</th><th>Status</th><th>Page</th><th>Evidence</th><th></th>`
      + `</tr></thead><tbody>${body || '<tr><td colspan="6" class="muted">no records</td></tr>'}</tbody></table></div>`;
  }

  return { opts, openPanel };
}
