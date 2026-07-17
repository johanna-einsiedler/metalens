// Account widget for the topbar: signed-in email + logout, or a "Sign in" button
// opening a login/register modal. On success it reloads so owned data appears
// (register/login also claim the anon session's work via X-Session-Id).
import { api } from "/static/api.js";
import { esc } from "/static/grammar.js";

export async function mountAccount(sel = "#account") {
  const el = document.querySelector(sel);
  if (!el) return;
  const me = await api.me();
  // signed-in users get a "My Workspace" nav entry in the PERSONAL group (first nav)
  const nav = document.querySelector(".topbar nav.nav-personal") || document.querySelector(".topbar nav");
  if (nav && me && me.email && !nav.querySelector("#nav-mydata")) {
    const a = document.createElement("a");
    a.id = "nav-mydata"; a.href = "/projects"; a.textContent = "My Workspace";
    nav.appendChild(a);
  }
  // mark the active nav item by path (nav links carry no hardcoded active class)
  const path = location.pathname;
  const mine = ["/projects", "/dataset", "/preset"].some((p) => path.startsWith(p));
  document.querySelectorAll(".topbar nav a").forEach((a) => {
    const href = a.getAttribute("href");
    if (!href) return;
    if (a.id === "nav-mydata") { if (mine) a.classList.add("active"); }
    else if (href === path) a.classList.add("active");
  });
  if (me && me.email) {
    // show the signed-in identity (links to account settings) + logout
    el.innerHTML = `<a class="acct" href="/account" title="Account settings">👤 ${esc(me.email)}</a> `
      + `<a href="#" id="pl-logout">Logout</a>`;
    el.querySelector("#pl-logout").onclick = async (e) => {
      e.preventDefault(); await api.logout(); location.reload();
    };
  } else {
    el.innerHTML = `<button class="btn btn-ghost" id="pl-signin">Sign in</button>`;
    el.querySelector("#pl-signin").onclick = openModal;
  }
}

function openModal() {
  const ov = document.createElement("div");
  ov.className = "modal-overlay";
  ov.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true">
      <h3 id="pl-title"></h3>
      <p class="muted">Owns your datasets &amp; verifications; anonymous work is claimed on sign-in.</p>
      <form id="pl-form">
        <input id="pl-email" type="text" placeholder="email or username" autocomplete="username" required/>
        <input id="pl-pw" type="password" placeholder="password" autocomplete="current-password" required/>
        <div id="pl-err" class="pl-err"></div>
        <div class="modal-actions">
          <button type="button" class="btn btn-ghost" id="pl-cancel">Cancel</button>
          <button type="submit" class="btn btn-primary" id="pl-submit"></button>
        </div>
      </form>
      <p class="muted pl-toggle"></p>
    </div>`;
  document.body.appendChild(ov);

  let mode = "login";
  const close = () => ov.remove();
  const render = () => {
    const reg = mode === "register";
    ov.querySelector("#pl-title").textContent = reg ? "Create account" : "Sign in";
    ov.querySelector("#pl-submit").textContent = reg ? "Create account" : "Sign in";
    ov.querySelector(".pl-toggle").innerHTML = reg
      ? 'Have an account? <a href="#" id="pl-switch">Sign in</a>'
      : 'No account? <a href="#" id="pl-switch">Create one</a>';
    ov.querySelector("#pl-switch").onclick = (e) => {
      e.preventDefault(); mode = reg ? "login" : "register"; render();
    };
  };
  render();

  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });
  ov.querySelector("#pl-cancel").onclick = close;
  ov.querySelector("#pl-form").onsubmit = async (e) => {
    e.preventDefault();
    const email = ov.querySelector("#pl-email").value.trim();
    const pw = ov.querySelector("#pl-pw").value;
    const err = ov.querySelector("#pl-err");
    const btn = ov.querySelector("#pl-submit");
    btn.disabled = true; err.textContent = "";
    try {
      await (mode === "register" ? api.register(email, pw) : api.login(email, pw));
      location.reload();
    } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
  };
  setTimeout(() => ov.querySelector("#pl-email").focus(), 0);
}
