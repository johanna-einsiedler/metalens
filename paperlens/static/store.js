// Filter state ⇄ URLSearchParams. The URL *is* the state → free deep-linking,
// back-button, and shareable filtered views. Emits onChange(state).
const KEYS = ["q", "schema", "jel", "topic", "status", "year", "dataset", "offset"];

export function createStore(onChange) {
  let state = fromURL();

  function fromURL() {
    const u = new URLSearchParams(location.search);
    const s = {};
    for (const k of KEYS) {
      const vals = u.getAll(k);
      if (!vals.length) continue;
      s[k] = k === "dataset" ? vals : vals[0];
    }
    return s;
  }

  function toURL() {
    const u = new URLSearchParams();
    for (const [k, v] of Object.entries(state)) {
      if (v == null || v === "") continue;
      if (Array.isArray(v)) v.forEach((x) => u.append(k, x));
      else u.set(k, String(v));
    }
    history.pushState(state, "", `${location.pathname}?${u.toString()}`);
  }

  function emit() { onChange({ ...state }); }

  function set(k, v) {
    if (v == null || v === "") delete state[k]; else state[k] = v;
    if (k !== "offset") delete state.offset;   // any filter change resets paging
    toURL(); emit();
  }
  function toggle(k, v) {
    const cur = state[k];
    if (cur === v || cur === String(v)) set(k, null); else set(k, v);
  }
  function get() { return { ...state }; }

  window.addEventListener("popstate", () => { state = fromURL(); emit(); });
  return { get, set, toggle, emit };
}
