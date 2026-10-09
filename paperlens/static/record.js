// /record?id=<record id> — one record of a public dataset, checked against the paper by a reader.
//
// The payload is the workspace's document view cut to what this viewer may see (review.py); the
// card, the evidence index and the PDF overlay are the workspace's own modules, used read-only.
// The reader drops THEIR copy of the PDF: the quotes are located in it, a clicked number is
// pinpointed in it, and their verdict goes to the public record as a reader check — counted and
// shown, never adopted as the maintainer's status.
import { api } from "/static/api.js";
import { renderFields, renderChild, renderConfBadge, renderValue, formatKey, esc } from "/static/grammar.js";
import { viewModel, indexEvidence, evidenceFor, entryTitle, undeclared } from "/static/spec.js";
import { renderPages, jumpToEvidence, showEvidence, hideEvidence, flashRects, setContextEvidence } from "/static/pdfview.js";

const $ = (s) => document.querySelector(s);
const NUM_RE = /^-?\d[\d,]*(\.\d+)?%?$/;
let DATA = null, VM = null, EV = null, REC = null;

const recordId = () => new URLSearchParams(location.search).get("id");
const minutesText = (m) => (m % 60 === 0 ? `${m / 60} hour${m === 60 ? "" : "s"}` : `${m} minutes`);

// what the right pane shows: the same document with the same copy keeps its pages when the
// reader moves to another record of the paper or records a check — only the panel is redrawn
const paneKey = () => (DATA ? `${DATA.document_id}|${DATA.review ? DATA.review.token : "own"}|${DATA.pages.length}` : "");

async function load(id, { push = false } = {}) {
  const before = paneKey();
  if (!before) $("#panelbody").innerHTML = '<p class="muted">Loading…</p>';
  try { DATA = await api.recordReview(id); }
  catch (e) {
    $("#panelbody").innerHTML = `<p class="muted">${esc(/404/.test(e.message)
      ? "This record is not public, or does not exist. A record page opens for the records of public datasets only."
      : e.message)}</p>`;
    $("#pages").innerHTML = ""; $("#copybar").hidden = true; $("#pdfzoom").hidden = true;
    return;
  }
  if (push) history.pushState({}, "", `/record?id=${encodeURIComponent(id)}`);
  rebuild();
  if (before && before === paneKey()) {
    renderPanel();
    if (DATA.pages.length) { setContextEvidence(EV.byRecord.get(REC.id) || []); jumpToFirst(); }
    $("#panel").scrollTop = 0;
  } else render();
}

function jumpToFirst() {
  const mine = EV.entryCites.get(REC.id) || EV.byRecord.get(REC.id) || [];
  const first = mine.find((i) => DATA.evidence[i].rect && DATA.evidence[i].rect.length);
  if (first !== undefined) setTimeout(() => jumpToEvidence(DATA.evidence[first].page, first, { flash: false }), 200);
}

function rebuild() {
  VM = viewModel(DATA); EV = indexEvidence(DATA, VM);
  REC = DATA.records.find((r) => r.id === DATA.focus) || DATA.records[0];
}

function render() { renderPanel(); renderPdf(); }

// ── the left panel: paper, the record, the reader's check, history, the paper's other records ──
function renderPanel() {
  const host = $("#panelbody");
  const p = DATA.paper || {}, ds = DATA.dataset, v = DATA.viewer;
  const authors = Array.isArray(p.authors) ? p.authors.slice(0, 3).join(", ") + (p.authors.length > 3 ? " et al." : "") : p.authors;
  const sub = [authors, p.year, p.journal].filter(Boolean).join(" · ");
  let html = `<div class="rec-head">`
    + (ds ? `<div class="rec-crumb"><a href="/dataset?id=${esc(ds.id)}">${esc(ds.title || "dataset")}</a> <span class="muted">· one ${esc(VM.entries.label.toLowerCase())}</span></div>` : "")
    + `<div class="rec-paper"><h2>${esc(p.title || "Untitled paper")}</h2><div class="meta">${esc(sub)}`
    + (p.doi ? `${sub ? " · " : ""}<a href="https://doi.org/${esc(p.doi)}" target="_blank" rel="noopener">${esc(p.doi)}</a>` : "") + `</div></div></div>`;
  if (!REC) {
    host.innerHTML = html + '<p class="muted">This paper has no public records.</p>';
    return;
  }
  if (v.owner) {
    html += `<div class="rec-banner">You own this paper. <a href="/workspace?doc=${esc(DATA.document_id)}&rec=${esc(REC.id)}">Open it in the workspace →</a> to verify or correct it — this page is what a reader sees.</div>`;
  } else {
    html += `<div class="rec-banner">${DATA.review ? "The quotes are highlighted in <b>your</b> copy of the paper. "
      : "What the extractor read from the paper, with the passages it cited. Drop your own copy of the PDF on the right to see each quote in it. "}`
      + `Your check is recorded as a <b>reader check</b> — it never changes the maintainer's verdict.`
      + (v.anonymous ? ` <a href="/account">Sign in</a> to have your checks attributed to you.` : "") + `</div>`;
  }
  html += renderCard(REC) + renderCheck() + renderHistory(DATA.events) + renderSiblings();
  host.innerHTML = html;
  wirePanel(host);
}

// the declared layout, read-only: tabs → badges → fields → sub-entries, an "Other" tab for
// anything undeclared (the workspace's renderEntryBody without the editing)
function renderCard(rec) {
  const fv = rec.field_values || {};
  const eopts = { editable: false };
  const tabs = VM.tabs.map((t) => {
    const defs = t.fields.map((n) => VM.fieldIndex.get(n)).filter((x) => x && x.scope === "entry").map((x) => x.field);
    const children = t.fields.map((n) => VM.fieldIndex.get(n)).filter((x) => x && x.scope === "child_key").map((x) => x.child);
    const badges = (t.groups || []).filter((gid) => (VM.groups[gid] || {}).scope !== "child")
      .map((gid) => renderConfBadge(gid, VM.groups[gid], (rec.confidence || {})[gid], VM.levels)).join("");
    let html = badges ? `<div class="group-head">${badges}</div>` : "";
    if (defs.length) html += `<div class="rv-root">${renderFields(defs, fv, "", eopts)}</div>`;
    for (const ch of children) {
      const cites = EV.tableCites.get(`${rec.id}|${ch.key}`);
      html += `<div class="rv-row rv-row-block rv-decl" data-field="${esc(ch.key)}"><div class="rv-key" data-path="${esc(ch.key)}" title="${esc(ch.help || "")}">`
        + `${esc(ch.label || formatKey(ch.key))}${Array.isArray(fv[ch.key]) ? ` <span class="muted">(${fv[ch.key].length})</span>` : ""}`
        + (cites ? ` <button type="button" class="ev-cite" data-eids="${cites.join(",")}" data-page="${DATA.evidence[cites[0]].page || 1}">p.${DATA.evidence[cites[0]].page || "?"}</button>` : "")
        + `</div><div class="rv-val rv-nested">${renderChild(ch, fv[ch.key], eopts)}</div></div>`;
    }
    return { t, html };
  });
  const other = undeclared(VM, "entry", fv);
  if (other.length) {
    const extra = {}; other.forEach((k) => (extra[k] = fv[k]));
    tabs.push({ t: { id: "_extra", label: "Other" }, html: `<p class="muted" style="font-size:12px;margin:0 0 6px">Returned by the model but not declared by the preset.</p>${renderValue(extra, eopts)}` });
  }
  const body = tabs.length === 1 ? `<div class="subpanels"><div class="subpanel" data-vi="0">${tabs[0].html}</div></div>`
    : `<div class="subtabs">${tabs.map(({ t }, i) => `<button class="subtab${i === 0 ? " active" : ""}" data-vi="${i}">${esc(t.label)}</button>`).join("")}</div>`
      + `<div class="subpanels">${tabs.map(({ html }, i) => `<div class="subpanel${i === 0 ? "" : " hidden"}" data-vi="${i}">${html}</div>`).join("")}</div>`;
  const corr = (rec.corrections || []).length;
  const note = rec.note ? `<div class="issue-note">“${esc(rec.note.text)}” <span class="muted">— ${esc(rec.note.by || "")}, ${esc(rec.note.status)}</span></div>` : "";
  const ev = EV.byRecord.get(rec.id) || [];
  const unit = VM.entries.label.toLowerCase();
  return `<div class="record" data-rid="${esc(rec.id)}"><div class="rectitle">${esc(entryTitle(VM, rec, rec.entry_index))}`
    + `<span class="status ${esc(rec.verification_status)}" title="the maintainer's verdict">${esc(rec.verification_status)}</span>`
    + (corr ? `<span class="muted" style="font-size:11px" title="corrected by the maintainer">✎ ${corr}</span>` : "") + readerPill() + `</div>${note}${body}`
    + `<div class="ev-list${DATA.pages.length ? " live" : ""}">`
    + (ev.length ? `<div class="muted" style="font-size:11.5px;margin-top:10px">${ev.length} passage${ev.length === 1 ? "" : "s"} cited for this ${esc(unit)}${DATA.pages.length ? " — click one to see it" : ""}</div>` + ev.map(evBlock).join("")
      : `<div class="muted" style="font-size:11.5px;margin-top:10px">No passage was cited for this ${esc(unit)}.</div>`)
    + `</div></div>`;
}

function evBlock(i) {
  const e = DATA.evidence[i], unfound = DATA.pages.length && !(e.rect && e.rect.length);
  return `<div class="ev-block${unfound ? " unfound" : ""}" data-eid="${i}" data-page="${e.page || 1}"><p class="ev-snippet">${esc(e.snippet || "")}</p><div class="ev-tags">`
    + (e.page ? `<span class="ev-tag ev-page">p${e.page}</span>` : "") + (e.source ? `<span class="ev-tag ev-source">${esc(e.source)}</span>` : "")
    + (unfound ? `<span class="ev-tag" title="these words were not found in your copy — a different edition, a scanned page, or a paraphrase">not found in your copy</span>` : "") + `</div></div>`;
}

function readerPill() {
  const t = DATA.readers || {};
  if (!t.verified && !t.flagged) return "";
  const parts = [t.verified ? `✓ ${t.verified}` : "", t.flagged ? `⚑ ${t.flagged}` : ""].filter(Boolean).join(" · ");
  return `<span class="rec-tally" title="reader checks — ${t.same_pdf} against the very same PDF file">readers ${parts}</span>`;
}

function renderCheck() {
  if (DATA.viewer.owner) return "";
  const scalars = VM.entries.fields.filter((f) => ["string", "text", "integer", "number", "boolean", "enum"].includes(f.type));
  const rv = DATA.review;
  const against = !rv ? "Drop the PDF first to see the quotes in the paper; a check made without it is recorded as such."
    : rv.match === "same" ? "Checked against your copy — the same file the extractor read."
    : rv.match === "different" ? "Checked against your copy — a different file from the extractor's, so a passage may sit elsewhere or be worded differently."
    : "Checked against your copy (the extractor's file has no recorded hash to compare with).";
  return `<div class="rec-check"><h4>Your check</h4><p class="muted" style="font-size:12px;margin:0">${esc(against)}</p>`
    + `<div class="verify" style="margin-top:8px"><button type="button" class="vbtn ok" data-status="verified">✓ Checks out</button><button type="button" class="vbtn flag" data-status="flagged">⚑ Something is off</button></div>`
    + `<textarea id="chknote" placeholder="What did you see? (optional for a confirmation, expected for a flag)"></textarea>`
    + `<div class="row" id="chkfix" hidden><span class="muted" style="font-size:12px">Propose a correction:</span>`
    + `<select id="chkfield"><option value="">— field —</option>${scalars.map((f) => `<option value="${esc(f.name)}">${esc(f.label || formatKey(f.name))}</option>`).join("")}</select>`
    + `<input type="text" id="chkvalue" placeholder="the value you read"/></div>`
    + `<div class="row"><button type="button" class="btn" id="chksend" disabled>Record my check</button><span class="muted" id="chkmsg" style="font-size:12px"></span></div></div>`;
}

function renderHistory(events) {
  const rows = (events || []).map((e) => {
    const when = (e.created_at || "").replace("T", " ").slice(0, 16);
    const rv = e.context && e.context.review;
    const ctx = rv ? ` · <span title="checked against the reader's own copy of the PDF">own PDF${rv.match === "same" ? ", same file" : rv.match === "different" ? ", different file" : ""}</span>` : "";
    const changes = Array.isArray(e.diff) ? e.diff.filter((d) => d.original_value !== d.final_value).map((d) =>
      `<div class="hist-diff"><code>${esc(d.field_path)}</code>: ${esc(String(d.original_value))} → <b>${esc(String(d.final_value))}</b>${e.verifier_kind === "community" ? ' <span class="muted">(proposed)</span>' : ""}</div>`).join("") : "";
    return `<div class="hist-row"><span class="hist-meta">${esc(e.status)} · ${esc(e.by || e.verifier_kind || "")} · ${esc(when)}${ctx}</span>`
      + (e.notes ? `<div class="hist-note">${esc(e.notes)}</div>` : "") + changes + `</div>`;
  }).join("");
  return `<div class="rec-hist"><h4>History</h4>${rows || '<span class="muted" style="font-size:12px">Nothing recorded on this entry yet.</span>'}</div>`;
}

function renderSiblings() {
  const others = DATA.records.filter((r) => r.id !== REC.id);
  if (!others.length) return "";
  const unit = VM.entries.label.toLowerCase();
  return `<div class="rec-siblings"><h4>${others.length} other ${esc(unit)}${others.length === 1 ? "" : "s"} from this paper${DATA.dataset ? " in this dataset" : ""}</h4>`
    + others.map((r) => `<a href="/record?id=${esc(r.id)}" data-rid="${esc(r.id)}">${esc(entryTitle(VM, r, r.entry_index))} <span class="status ${esc(r.verification_status)}">${esc(r.verification_status)}</span></a>`).join("") + `</div>`;
}

function wirePanel(host) {
  host.querySelectorAll(".subtab").forEach((b) => (b.onclick = () => {
    host.querySelectorAll(".subtab").forEach((x) => x.classList.toggle("active", x === b));
    host.querySelectorAll(".subpanel").forEach((p) => p.classList.toggle("hidden", p.dataset.vi !== b.dataset.vi));
  }));
  const card = host.querySelector(".record");
  if (card) linkCells(card, REC);
  host.querySelectorAll(".rec-siblings a[data-rid]").forEach((a) => (a.onclick = (e) => { e.preventDefault(); load(a.dataset.rid, { push: true }); }));
  if (DATA.pages.length) host.querySelectorAll(".ev-list .ev-block").forEach((b) => {
    const i = +b.dataset.eid;
    b.onclick = () => jumpToEvidence(+b.dataset.page, i);
    b.onmouseenter = () => showEvidence([i]); b.onmouseleave = () => hideEvidence([i]);
  });
  wireCheck(host);
}

// ── value ↔ evidence linking (the workspace's linkCells, read-only) ──────────────────────────
function isPageRef(path) {
  const leaf = (path || "").split(".").pop().replace(/\[\d+\]$/, "");
  return leaf === "page" || leaf === "evidence_page" || leaf.endsWith("_page");
}
// a row cited several times: a click on a NUMBER lands on the citation whose snippet carries it
function citeFor(cell, ids) {
  const txt = cell.textContent.trim().replace(/%$/, "");
  if (!NUM_RE.test(txt) || ids.length < 2) return ids[0];
  const bare = txt.replace(/,/g, "");
  const hit = ids.find((i) => new RegExp(`(^|[^0-9.])${bare.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![0-9])`).test(String(DATA.evidence[i].snippet || "").replace(/,/g, "")));
  return hit === undefined ? ids[0] : hit;
}

function linkCells(card, rec) {
  const live = DATA.pages.length > 0;
  card.querySelectorAll("[data-path]").forEach((cell) => {
    const p = cell.dataset.path;
    const leaf = p.split(".").pop().replace(/\[\d+\]$/, "");
    const info = VM.fieldIndex.get(leaf);
    const def = info && info.field ? info.field : null;
    const hit = evidenceFor(EV, VM, rec.id, p);
    const isKey = cell.classList.contains("rv-key");
    const hasValue = !isKey && cell.classList.contains("rv-cell") && !cell.classList.contains("rv-missing") && cell.textContent.trim() !== "";
    const wantsOwn = def && def.evidence === "value";
    if (hit) {
      const exact = hit.kind === "exact";
      const anyRect = hit.ids.some((i) => EV.hasRect[i]);
      if (isKey) cell.classList.add("rv-linked");
      else if (exact) cell.classList.add(!live || anyRect ? "rv-cited" : "rv-cited-nofix");
      else if (wantsOwn && hasValue) cell.classList.add("rv-uncited", "rv-covered");
      else cell.classList.add("rv-covered");
      if (!live) return;
      cell.addEventListener("mouseenter", () => showEvidence(hit.ids));
      cell.addEventListener("mouseleave", () => hideEvidence(hit.ids));
      cell.addEventListener("click", () => jumpAndRefine(cell, hit));
      return;
    }
    if (wantsOwn && hasValue) cell.classList.add("rv-uncited");
    // no citation: a number can still be searched for in the reader's copy
    if (live && DATA.review && cell.classList.contains("rv-num") && !isPageRef(p)) {
      cell.classList.add("rv-probe");
      cell.addEventListener("click", () => locateAnywhere(cell));
    }
  });
  card.querySelectorAll(".rv-rowcite[data-rowpath]").forEach((slot) => {
    const ids = EV.rowCites.get(`${rec.id}|${slot.dataset.rowpath}`);
    slot.innerHTML = ids
      ? `<button type="button" class="ev-cite" data-eids="${ids.join(",")}" data-page="${DATA.evidence[ids[0]].page || 1}" title="${esc(DATA.evidence[ids[0]].snippet || "")}">p.${DATA.evidence[ids[0]].page || "?"}</button>`
      : `<span class="ev-cite ev-none" title="no citation for this row">–</span>`;
  });
  card.querySelectorAll(".ev-cite[data-eids]").forEach((b) => {
    if (!live) { b.disabled = true; return; }
    const ids = b.dataset.eids.split(",").map(Number);
    b.onclick = (e) => { e.stopPropagation(); jumpToEvidence(+b.dataset.page, ids); };
    b.onmouseenter = () => showEvidence(ids); b.onmouseleave = () => hideEvidence(ids);
  });
}

// jump to the cited passage at once; then, for a number, pinpoint it inside the citation —
// the row's band for a row citation, the cited page for a table, anywhere for an exact quote
async function jumpAndRefine(cell, hit) {
  const page = DATA.evidence[citeFor(cell, hit.ids)].page;
  const txt = cell.textContent.trim();
  const chased = NUM_RE.test(txt) && !isPageRef(cell.dataset.path) && !!DATA.review;
  jumpToEvidence(page, hit.ids, { flash: !(chased && VM.citationFlash === false) });
  if (!chased) return;
  const num = txt.replace(/%$/, "");
  try {
    let r;
    if (hit.kind === "exact") r = await api.reviewLocate(DATA.review.token, num, page);
    else if (hit.kind === "row") {
      const bands = hit.ids.flatMap((i) => (DATA.evidence[i].page === page ? (DATA.evidence[i].rect || []) : [])).map(([, y, , h]) => `${Math.round(y)}:${Math.round(y + h)}`);
      if (!bands.length) return;
      r = await api.reviewLocate(DATA.review.token, num, page, bands.join(","));
    } else r = await api.reviewLocate(DATA.review.token, num, page, null, { pageOnly: true });
    if (r && r.found) { flashRects(r.page || page, r.rects, { keep: hit.kind !== "exact" }); return; }
    if (hit.kind === "exact" && !(r && r.no_pdf)) softNote(cell);
  } catch { /* the passage highlight stands */ }
}
async function locateAnywhere(cell) {
  const num = cell.textContent.trim().replace(/%$/, "");
  try {
    const r = await api.reviewLocate(DATA.review.token, num, 1);
    if (r && r.found) flashRects(r.page || 1, r.rects); else softNote(cell);
  } catch { /* nothing to show */ }
}
function softNote(cell) {
  if (cell.nextElementSibling && cell.nextElementSibling.classList.contains("val-check")) return;
  cell.insertAdjacentHTML("afterend", `<span class="val-check" title="This exact number is not in your copy's text — it may be rounded, transformed, computed from other values, or your copy may differ. Not necessarily wrong.">⚠ not found verbatim</span>`);
}

// ── the reader's check ───────────────────────────────────────────────────────────────────────
function wireCheck(host) {
  const box = host.querySelector(".rec-check");
  if (!box) return;
  let status = null;
  const send = box.querySelector("#chksend"), fix = box.querySelector("#chkfix"), msg = box.querySelector("#chkmsg");
  box.querySelectorAll(".vbtn[data-status]").forEach((b) => (b.onclick = () => {
    status = b.dataset.status;
    box.querySelectorAll(".vbtn").forEach((x) => x.classList.toggle("active", x === b));
    fix.hidden = status !== "flagged"; send.disabled = false; msg.textContent = "";
  }));
  send.onclick = async () => {
    if (!status) return;
    const notes = box.querySelector("#chknote").value.trim() || null;
    const body = { status, notes };
    if (DATA.review) body.review_token = DATA.review.token;
    if (status === "flagged") {
      const field = box.querySelector("#chkfield").value, raw = box.querySelector("#chkvalue").value.trim();
      if (field && raw) {
        const def = VM.fieldIndex.get(field).field;
        const val = (def.type === "number" || def.type === "integer") && NUM_RE.test(raw) ? Number(raw.replace(/,/g, "")) : def.type === "boolean" ? /^(true|yes|1)$/i.test(raw) : raw;
        body.diff = [{ field_path: field, original_value: (REC.field_values || {})[field] ?? null, final_value: val }];
      }
    }
    send.disabled = true; msg.textContent = "recording…";
    try {
      await api.verify(REC.id, body);
      await load(REC.id);
      const m = $("#chkmsg"); if (m) m.textContent = "Recorded — thank you.";
      $("#panel").scrollTop = 0;
    } catch (e) { send.disabled = false; msg.textContent = e.message; }
  };
}

// ── the right pane: the reader's copy, or the invitation to drop one ─────────────────────────
function renderPdf() {
  const pane = $("#pages"), zoom = $("#pdfzoom"), bar = $("#copybar");
  if (!REC) { pane.innerHTML = ""; bar.hidden = true; zoom.hidden = true; return; }
  if (DATA.pages.length) {
    zoom.hidden = false;
    bar.hidden = !DATA.review;
    if (DATA.review) { bar.innerHTML = copyBarHtml(); wireCopyBar(bar); }
    renderPages(pane, DATA.pages, DATA.evidence);
    setContextEvidence(EV.byRecord.get(REC.id) || []);
    jumpToFirst();
    return;
  }
  zoom.hidden = true; bar.hidden = true;
  if (DATA.viewer.owner) { pane.innerHTML = '<p class="muted" style="padding:18px">No page images are stored for this paper.</p>'; return; }
  pane.innerHTML = dropHtml();
  wireDrop(pane);
}

function copyBarHtml() {
  const rv = DATA.review;
  const match = rv.match === "same" ? `<span class="pill same" title="your file has the same hash as the one the extractor read">same file as the extractor's</span>`
    : rv.match === "different" ? `<span class="pill different" title="your file differs from the one the extractor read — another edition or a re-saved copy">different file from the extractor's</span>`
    : `<span class="pill">no hash on record to compare with</span>`;
  const scanned = (DATA.scanned_pages || []).length ? `<span class="muted" title="pages ${DATA.scanned_pages.join(", ")}">${DATA.scanned_pages.length} scanned page${DATA.scanned_pages.length === 1 ? "" : "s"} (no text to search)</span>` : "";
  return `<span>Your copy · ${rv.n_pages} page${rv.n_pages === 1 ? "" : "s"}</span>${match}${scanned}<span class="spacer"></span>`
    + `<span class="muted" title="deleted ${esc(minutesText(rv.retention_minutes))} after you last use it, or now">kept for you alone</span><button type="button" id="copyforget">Remove my copy</button>`;
}
function wireCopyBar(bar) {
  bar.querySelector("#copyforget").onclick = async () => {
    try { await api.reviewForget(DATA.review.token); } catch { /* it may have expired already */ }
    load(REC.id);
  };
}

function dropHtml() {
  const n = (DATA.evidence || []).length;
  return `<div class="drop" id="drop"><h3>See the quotes in the paper</h3>`
    + `<p>Drop your copy of the PDF here, or <label class="btn" for="pdffile">choose a file</label><input type="file" id="pdffile" accept="application/pdf"/></p>`
    + `<p>Metalens never shares the dataset owner's PDF. Your copy is compared with it by hash, its pages rendered for you alone, the ${n} cited passage${n === 1 ? "" : "s"} located in it — and it is deleted ${esc(minutesText(120))} after you last use it, or the moment you ask.</p>`
    + `<p id="dropmsg"></p></div>`;
}
function wireDrop(pane) {
  const zone = pane.querySelector("#drop"), input = pane.querySelector("#pdffile"), msg = pane.querySelector("#dropmsg");
  const upload = async (file) => {
    if (!file) return;
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") { msg.textContent = "That is not a PDF."; return; }
    msg.innerHTML = `<span class="busy">Rendering the pages and locating the passages in your copy…</span>`;
    zone.classList.remove("over");
    try {
      DATA = await api.reviewUpload(REC.id, file);
      rebuild(); render();
    } catch (e) { msg.textContent = e.message.replace(/^\d+ /, ""); }
  };
  input.onchange = () => upload(input.files[0]);
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
  zone.addEventListener("drop", (e) => upload(e.dataTransfer.files && e.dataTransfer.files[0]));
}

function mountPdfZoom() {
  const pages = $("#pages"), box = $("#pdfzoom"), val = $("#pdfzoomval");
  let z = parseFloat(localStorage.getItem("metalens_pdf_zoom") || "1") || 1;
  const apply = () => {
    z = Math.min(4, Math.max(0.5, Math.round(z * 100) / 100));
    pages.style.setProperty("--pdf-zoom", String(z));
    val.textContent = z === 1 ? "fit" : `${Math.round(z * 100)}%`;
    try { localStorage.setItem("metalens_pdf_zoom", String(z)); } catch { /* private mode */ }
  };
  box.querySelectorAll("button[data-z]").forEach((b) => (b.onclick = () => { z = b.dataset.z === "in" ? z * 1.25 : b.dataset.z === "out" ? z / 1.25 : 1; apply(); }));
  pages.addEventListener("wheel", (e) => { if (!(e.ctrlKey || e.metaKey)) return; e.preventDefault(); z = e.deltaY < 0 ? z * 1.1 : z / 1.1; apply(); }, { passive: false });
  apply();
}

mountPdfZoom();
window.addEventListener("popstate", () => { const id = recordId(); if (id) load(id); });
const id = recordId();
if (id) load(id);
else $("#panelbody").innerHTML = '<p class="muted">No record given. Open this page from a dashboard or a dataset: <code>/record?id=&lt;record id&gt;</code>.</p>';
