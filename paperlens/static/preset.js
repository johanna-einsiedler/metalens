// /preset — the public CARD of a curated preset (?id=<bundled preset>: what it extracts, how it
// was verified, how to cite it, what was built with it), or the personal-preset EDITOR (?edit=1,
// or no id at all). The editor's code stays out of a reader's page load.
const q = new URLSearchParams(location.search);
const id = q.get("id");
if (!id || q.get("edit") === "1") import("/static/preset_editor.js").then((m) => m.init(id));
else import("/static/preset_card.js").then((m) => m.init(id));
