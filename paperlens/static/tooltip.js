// Floating provenance tooltip for dashboard marks. Reuses the .tip-pop token styling
// (base.css); .tip-rich allows HTML content. One shared node, cursor-positioned + clamped.
let TIP = null;

function el() {
  if (TIP) return TIP;
  TIP = document.createElement("div");
  TIP.className = "tip-pop tip-rich";
  document.body.appendChild(TIP);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });
  return TIP;
}

export function showTip(html, x, y) {
  const t = el();
  t.innerHTML = html;
  t.style.display = "block";
  const w = t.offsetWidth, h = t.offsetHeight, pad = 14;
  let left = x + pad, top = y + pad;
  if (left + w > window.innerWidth - 8) left = x - w - pad;
  if (top + h > window.innerHeight - 8) top = window.innerHeight - h - 8;
  t.style.left = Math.max(8, left) + "px";
  t.style.top = Math.max(8, top) + "px";
}

export function hideTip() { if (TIP) TIP.style.display = "none"; }
