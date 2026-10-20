// The MASEMiner builder's colour-coded prompt: what the template says (beige) and what the user's
// entries put into it (blue), as in the workflow figure of the MASEMiner paper. The server cuts the
// rendered prompt into segments (/api/build-preset-prompt with segments: true →
// preset_spec.render_prompt_segments); this module paints them. After a hand edit the colours are
// carried over by a word diff against the last generated prompt: unchanged words keep their
// colour, words typed by hand count as the user's.

const escH = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function classOf(seg) {
  if (seg.kind === "param") return seg.given ? "pp-u" : "pp-u pp-ex";
  if (seg.kind === "edit") return "pp-u pp-ed";
  return "pp-t";
}
function tipOf(seg) {
  if (seg.kind === "param") return seg.given ? `Your entry: ${seg.label}`
    : `${seg.label}: the preset's example, used until you fill in this field`;
  if (seg.kind === "edit") return "Typed by hand";
  if (seg.param) return `Template text while “${seg.label}” is empty; your entry replaces it`;
  return "";
}

// segments → HTML for a <pre>: each line's text is wrapped on its own, so blank lines and the
// indentation stay unpainted; a markdown heading of the template is bold, not highlighted.
export function paintPrompt(segs) {
  let html = "", atStart = true;
  for (const seg of segs) {
    const cls = classOf(seg), tip = tipOf(seg), title = tip ? ` title="${escH(tip)}"` : "";
    seg.text.split("\n").forEach((line, i) => {
      if (i > 0) { html += "\n"; atStart = true; }
      if (!line) return;
      const [, lead, core, trail] = line.match(/^(\s*)([\s\S]*?)(\s*)$/);
      if (!core) { html += escH(line); return; }
      if (seg.kind === "template" && atStart && !lead && /^#{1,6}\s/.test(core)) html += `<b class="pp-h">${escH(core)}</b>${escH(trail)}`;
      else html += `${escH(lead)}<span class="${cls}"${title}>${escH(core)}</span>${escH(trail)}`;
      atStart = false;
    });
  }
  return html;
}

const tokens = (s) => s.match(/\s+|\S+/g) || [];

// the longest common subsequence of two token lists, as matched [i, j] pairs in order
function lcsPairs(a, b) {
  const n = a.length, m = b.length, W = m + 1;
  const dp = new Uint16Array((n + 1) * W);       // min(n, m) <= sqrt(n*m) <= 1733 fits
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i * W + j] = a[i] === b[j] ? dp[(i + 1) * W + j + 1] + 1 : Math.max(dp[(i + 1) * W + j], dp[i * W + j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push([i, j]); i++; j++; }
    else if (dp[(i + 1) * W + j] >= dp[i * W + j + 1]) i++;
    else j++;
  }
  return out;
}

const MAX_CELLS = 3e6;   // above this the changed middle counts as typed by hand as a whole

// the generated segments and the prompt as the user edited it → segments of the edited text:
// a word that survived keeps the kind of the segment it came from, a new word is kind "edit".
export function editedSegments(segs, text) {
  const oldText = segs.map((s) => s.text).join("");
  if (text === oldText) return segs;
  const owner = new Int32Array(oldText.length);
  let p = 0;
  segs.forEach((s, si) => { owner.fill(si, p, p + s.text.length); p += s.text.length; });
  const A = tokens(oldText), B = tokens(text), at = [];
  for (let k = 0, o = 0; k < A.length; o += A[k].length, k++) at.push(o);
  let pre = 0;
  while (pre < A.length && pre < B.length && A[pre] === B[pre]) pre++;
  let suf = 0;
  while (suf < A.length - pre && suf < B.length - pre && A[A.length - 1 - suf] === B[B.length - 1 - suf]) suf++;
  const a = A.slice(pre, A.length - suf), b = B.slice(pre, B.length - suf);
  const kept = new Map();                          // index in B → index in A
  for (let k = 0; k < pre; k++) kept.set(k, k);
  if (a.length * b.length <= MAX_CELLS) for (const [i, j] of lcsPairs(a, b)) kept.set(pre + j, pre + i);
  for (let k = 1; k <= suf; k++) kept.set(B.length - k, A.length - k);
  const out = [];
  const push = (t, origin) => {                    // origin: a segment index, or -1 for typed text
    if (!t) return;
    const last = out[out.length - 1];
    if (last && last.origin === origin) last.seg.text += t;
    else out.push({ origin, seg: origin < 0 ? { text: t, kind: "edit" } : { ...segs[origin], text: t } });
  };
  B.forEach((t, j) => {
    const i = kept.get(j);
    if (i === undefined) { push(t, -1); return; }
    let from = 0, run = owner[at[i]];              // a kept word may straddle two old segments
    for (let c = 1; c < t.length; c++)
      if (owner[at[i] + c] !== run) { push(t.slice(from, c), run); from = c; run = owner[at[i] + c]; }
    push(t.slice(from), run);
  });
  return out.map((x) => x.seg);
}
