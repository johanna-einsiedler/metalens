// Page chrome for every static page: the top bar (brand logo + title, the two nav groups,
// the account widget) painted from GET /api/brand, so one deployment can serve several
// product surfaces (Metalens, MASEMiner) by hostname without a copy of the header per page.
//
// Usage — every page keeps an EMPTY <header class="topbar" data-chrome></header> and ends with
//   <script type="module">import { mountChrome } from "/static/chrome.js"; mountChrome();</script>
// `getBrand()` gives other modules the same object (title, credit_label, default_preset …).
import { api } from "/static/api.js";
import { mountAccount } from "/static/auth.js";
import { esc } from "/static/grammar.js";

const CACHE_KEY = `brand:${location.host}`;
let brandPromise = null;

const FALLBACK = {
  id: "metalens", title: "Metalens", logo: "/static/logo.svg", credit_label: "Metalens credits",
  nav_shared: [{ href: "/dashboards", label: "Dashboards" }, { href: "/catalog", label: "Data Catalogue" }],
  nav_personal: [{ href: "/extract", label: "Process papers" }, { href: "/import", label: "Import" }, { href: "/workspace", label: "Data review" }],
  links: {}, tokens: {},
};

// The brand for this host: sessionStorage first (no flash after the first page), then the API.
export function getBrand() {
  if (brandPromise) return brandPromise;
  brandPromise = (async () => {
    let cached = null;
    try { cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null"); } catch { cached = null; }
    try {
      const fresh = await api.brand();
      try { sessionStorage.setItem(CACHE_KEY, JSON.stringify(fresh)); } catch { /* private mode */ }
      return fresh;
    } catch {
      return cached || FALLBACK;
    }
  })();
  return brandPromise;
}

function cachedBrand() {
  try { return JSON.parse(sessionStorage.getItem(CACHE_KEY) || "null"); } catch { return null; }
}

function paint(header, b) {
  const nav = (items, cls) => `<nav class="${cls}">${(items || []).map((n) => `<a href="${esc(n.href)}">${esc(n.label)}</a>`).join("")}</nav>`;
  header.innerHTML = `<a class="brand" href="/"><img src="${esc(b.logo)}" class="brand-logo" alt=""/>${esc(b.title)}</a>`
    + nav(b.nav_shared, "nav-shared") + `<span class="spacer"></span>` + nav(b.nav_personal, "nav-personal")
    + `<span id="account" class="account"></span>`;
  document.body.classList.add(`brand-${b.id}`);
  document.body.dataset.brand = b.id;
  for (const [k, v] of Object.entries(b.tokens || {})) document.documentElement.style.setProperty(k, v);
  // the page's own <title> is the page part; the brand goes in front
  const page = (document.title || "").replace(/^(Metalens|MASEMiner)\s*[—-]\s*/, "");
  document.title = page && page !== b.title ? `${b.title} — ${page}` : b.title;
}

export async function mountChrome(sel = "header.topbar[data-chrome]") {
  const header = document.querySelector(sel);
  if (!header) { await mountAccount(); return; }
  const cached = cachedBrand();
  if (cached) paint(header, cached);                 // instant, from the last page
  const b = await getBrand();
  paint(header, b);
  await mountAccount();                              // account widget, active nav item, retention note
}
