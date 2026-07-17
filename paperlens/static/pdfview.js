// PDF page images + SVG highlight overlays + click-to-source flash.
const SVGNS = "http://www.w3.org/2000/svg";

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
      (byPage[pg.page] || []).forEach(({ ev, i }) => {
        (ev.rect || []).forEach((r) => {
          const [x, y, w, h] = r;
          const rect = document.createElementNS(SVGNS, "rect");
          rect.setAttribute("x", x); rect.setAttribute("y", y);
          rect.setAttribute("width", w); rect.setAttribute("height", h);
          rect.setAttribute("class", "hl"); rect.dataset.eid = i;
          const t = document.createElementNS(SVGNS, "title");
          t.textContent = ev.snippet || ""; rect.appendChild(t);
          svg.appendChild(rect);
        });
      });
    };
    img.addEventListener("load", draw);
    img.src = pg.url;
    if (img.complete) draw();            // cached (Safari may not fire load) → draw now
    wrap.append(label, img, svg);
    root.appendChild(wrap);
  });
}

export function jumpToEvidence(page, eid) {
  document.querySelectorAll("rect.hl.flash").forEach((r) => r.classList.remove("flash"));
  const rects = [...document.querySelectorAll(`rect.hl[data-eid="${eid}"]`)];
  rects.forEach((r) => { r.classList.add("flash"); setTimeout(() => r.classList.remove("flash"), 2000); });
  scrollToRect(rects[0], page);   // land ON the evidence, not the top of the page
}

// pinpoint-highlight arbitrary rects on a page (e.g. a located numeric value) with
// the SAME yellow evidence-flash marking — temporary overlay rects that fade out.
export function flashRects(page, rects) {
  const wrap = document.getElementById(`page-${page}`);
  const svg = wrap && wrap.querySelector("svg.overlay");
  if (!svg) return;
  const drawn = (rects || []).map(([x, y, w, h]) => {
    const r = document.createElementNS(SVGNS, "rect");
    r.setAttribute("x", x); r.setAttribute("y", y);
    r.setAttribute("width", w); r.setAttribute("height", h);
    r.setAttribute("class", "hl flash");
    svg.appendChild(r);
    setTimeout(() => r.remove(), 2500);
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
  document.querySelectorAll(`rect.hl[data-eid="${eid}"]`).forEach((r) => r.classList.add("hot"));
}
export function hideEvidence(eid) {
  document.querySelectorAll(`rect.hl[data-eid="${eid}"]`).forEach((r) => r.classList.remove("hot"));
}
