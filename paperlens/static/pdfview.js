// PDF page images + SVG highlight overlays + click-to-source flash.
const SVGNS = "http://www.w3.org/2000/svg";
let CTX = new Set();   // evidence ids of the current entry / tab — applied to rects as they are drawn

export function renderPages(root, pages, evidence) {
  root.innerHTML = "";
  if (!pages || !pages.length) { root.innerHTML = '<p class="muted">No page images for this document.</p>'; return; }
  const byPage = {};
  (evidence || []).forEach((ev, i) => { (byPage[ev.page] ||= []).push({ ev, i }); });

  pages.forEach((pg) => {
    const wrap = document.createElement("div");
    wrap.className = "page"; wrap.id = `page-${pg.page}`;
    const label = document.createElement("div");
    label.className = "pagelabel"; label.textContent = `p${pg.page}`;
    const img = document.createElement("img");
    img.className = "pageimg"; img.alt = `page ${pg.page}`;
    const svg = document.createElementNS(SVGNS, "svg");
    svg.classList.add("overlay"); svg.setAttribute("preserveAspectRatio", "none");
    // Draw the highlight rects once the image size is known. Guarded + idempotent:
    // attach BEFORE setting src, and also call it if the image is already complete —
    // Safari fires `load` synchronously for cached images, so an onload set AFTER src
    // would miss the event and never draw the rectangles.
    let drawn = false;
    const draw = () => {
      if (drawn || !img.naturalWidth) return;
      drawn = true;
      svg.setAttribute("viewBox", `0 0 ${img.naturalWidth} ${img.naturalHeight}`);
      // The same rectangle is often cited many times (a reference row every condition points
      // at); nine translucent fills stacked make the text unreadable. Paint each distinct
      // rectangle ONCE and let it answer to every evidence id that cites it (`data-eids`).
      const merged = new Map();
      (byPage[pg.page] || []).forEach(({ ev, i }) => {
        (ev.rect || []).forEach((r) => {
          const key = r.map((v) => Math.round(v)).join(",");
          const m = merged.get(key) || { r, ids: [], snippets: [] };
          m.ids.push(String(i));
          if (ev.snippet && !m.snippets.includes(ev.snippet)) m.snippets.push(ev.snippet);
          merged.set(key, m);
        });
      });
      merged.forEach(({ r, ids, snippets }) => {
        const [x, y, w, h] = r;
        const rect = document.createElementNS(SVGNS, "rect");
        rect.setAttribute("x", x); rect.setAttribute("y", y);
        rect.setAttribute("width", w); rect.setAttribute("height", h);
        rect.setAttribute("class", "hl" + (ids.some((id) => CTX.has(id)) ? " ctx" : ""));
        rect.dataset.eid = ids[0]; rect.dataset.eids = ids.join(" ");
        const t = document.createElementNS(SVGNS, "title");
        t.textContent = snippets.join("\n"); rect.appendChild(t);
        svg.appendChild(rect);
      });
    };
    img.addEventListener("load", draw);
    img.src = pg.url;
    if (img.complete) draw();            // cached (Safari may not fire load) → draw now
    wrap.append(label, img, svg);
    root.appendChild(wrap);
  });
}

// Clear the current single selection: unpaint any selected/flashing pre-drawn rect, and
// remove any ad-hoc located-number rects entirely — so only the NEWEST pick stays visible.
// the drawn rects that answer to one evidence id (a rect may carry several ids)
const rectsFor = (id) => document.querySelectorAll(`rect.hl[data-eids~="${id}"]`);

function clearSelection() {
  document.querySelectorAll("rect.hl.sel, rect.hl.flash").forEach((r) => r.classList.remove("sel", "flash"));
  document.querySelectorAll("rect.hl.located").forEach((r) => r.remove());
}

// `eid` may be one evidence index or an array of them (a field cited twice lights both;
// nothing else). Only the newest selection is ever painted.
export function jumpToEvidence(page, eid) {
  clearSelection();               // replace the previous highlight — never stack them
  const ids = Array.isArray(eid) ? eid : [eid];
  const rects = ids.flatMap((id) => [...rectsFor(id)]);
  rects.forEach((r) => { r.classList.add("sel", "flash"); setTimeout(() => r.classList.remove("flash"), 1500); });
  scrollToRect(rects[0], page);   // land ON the evidence, not the top of the page
}

// Faintly mark the rects that belong to the CURRENT entry / tab (`.ctx`) so the coder sees
// where this entry's sources are on the page without the old wall of highlights; the
// strong `.sel` still marks the one item they clicked. null clears the context.
export function setContextEvidence(ids) {
  CTX = new Set((ids || []).map(String));
  document.querySelectorAll("rect.hl.ctx").forEach((r) => r.classList.remove("ctx"));
  if (!CTX.size) return;
  document.querySelectorAll("rect.hl[data-eids]").forEach((r) => { if (r.dataset.eids.split(" ").some((id) => CTX.has(id))) r.classList.add("ctx"); });
}

// pinpoint-highlight arbitrary rects on a page (e.g. a located numeric value). Like
// jumpToEvidence it keeps ONLY this selection: ad-hoc rects marked `.located` so the next
// pick clears them, and they persist (not a 2s fade) until then.
export function flashRects(page, rects, { keep = false } = {}) {
  if (keep) document.querySelectorAll("rect.hl.located").forEach((r) => r.remove());   // keep the cited row lit
  else clearSelection();
  const wrap = document.getElementById(`page-${page}`);
  const svg = wrap && wrap.querySelector("svg.overlay");
  if (!svg) return;
  const drawn = (rects || []).map(([x, y, w, h]) => {
    const r = document.createElementNS(SVGNS, "rect");
    r.setAttribute("x", x); r.setAttribute("y", y);
    r.setAttribute("width", w); r.setAttribute("height", h);
    r.setAttribute("class", "hl sel located flash");
    svg.appendChild(r);
    setTimeout(() => r.classList.remove("flash"), 1500);
    return r;
  });
  scrollToRect(drawn[0], page);
}

// Scroll so a specific highlight rect sits in the CENTER of the scroll viewport — so
// evidence low on a tall page is actually visible after the jump (not just the page top).
// Uses on-screen geometry (getBoundingClientRect handles the SVG viewBox scaling), and
// falls back to the page top when we have no rect.
function scrollToRect(rectEl, page) {
  const wrap = document.getElementById(`page-${page}`);
  if (!rectEl) { if (wrap) wrap.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
  let scroller = rectEl.parentElement;
  while (scroller) {
    const oy = getComputedStyle(scroller).overflowY;
    if ((oy === "auto" || oy === "scroll") && scroller.scrollHeight > scroller.clientHeight) break;
    scroller = scroller.parentElement;
  }
  if (!scroller) { rectEl.scrollIntoView({ behavior: "smooth", block: "center" }); return; }
  const rb = rectEl.getBoundingClientRect(), sb = scroller.getBoundingClientRect();
  const top = scroller.scrollTop + (rb.top - sb.top) - scroller.clientHeight / 2 + rb.height / 2;
  scroller.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
}

// hover preview — light up the rect(s) for an evidence id without scrolling
export function showEvidence(eid) {
  (Array.isArray(eid) ? eid : [eid]).forEach((id) =>
    rectsFor(id).forEach((r) => r.classList.add("hot")));
}
export function hideEvidence(eid) {
  (Array.isArray(eid) ? eid : [eid]).forEach((id) =>
    rectsFor(id).forEach((r) => r.classList.remove("hot")));
}
