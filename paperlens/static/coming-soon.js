// Shared "coming soon" placeholder for features temporarily disabled in the beta
// (the dashboard/analysis builder). Renders into a host element, reusing the
// token-styled .empty-state layout so no raw colours are introduced.
import { esc } from "/static/grammar.js";

export function renderComingSoon(host, { title = "Coming soon", note = "" } = {}) {
  if (!host) return;
  host.innerHTML =
    `<div class="empty-state">`
    + `<div style="font-size:34px;line-height:1">🚧</div>`
    + `<h2 style="margin:.4em 0 .3em">${esc(title)}</h2>`
    + (note ? `<p class="muted">${esc(note)}</p>` : "")
    + `<p style="margin-top:18px"><a class="btn btn-ghost" href="/workspace">← Back to data review</a></p>`
    + `</div>`;
}
