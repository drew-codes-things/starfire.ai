// streamingSegmenter.js
//
// Pure logic for incremental streaming-markdown rendering, in the spirit of
// odysseus-dev's static/js/streamingSegmenter.js: re-rendering the whole
// accumulated message on every token is wasteful and re-flows the DOM, so we
// FREEZE the leading part of the message that can no longer change (full
// paragraphs, closed code fences) and only re-render the growing tail.
//
// This is a simplified reimplementation, not a byte-for-byte port — odysseus's
// version proves exact equivalence against its own bespoke markdown.js
// renderer; starfire renders via the `marked` library instead, so the
// boundary rule here is intentionally conservative (only cut at a blank line
// or a closed fence, both of which are safe cut points for any standard
// CommonMark-ish renderer) rather than reusing odysseus's renderer-specific
// proof.

const FENCE_RE = /^ {0,3}(`{3,}|~{3,})/;

/**
 * Return the number of leading characters of `text` that are safe to freeze:
 * render(text) === render(text.slice(0, n)) + render(text.slice(n)).
 * Only advances to a blank-line boundary or the line after a closed fence,
 * and only when not currently inside an open fence.
 */
function splitFinalized(text) {
  let inFence = false;
  let fenceMarker = '';
  let lastSafeBoundary = 0;
  let i = 0;
  const n = text.length;

  while (i < n) {
    const nl = text.indexOf('\n', i);
    if (nl === -1) break; // incomplete trailing line — never a safe boundary
    const line = text.slice(i, nl);
    const afterNl = nl + 1;
    const fence = line.match(FENCE_RE);

    if (fence) {
      const marker = fence[1];
      if (!inFence) {
        inFence = true;
        fenceMarker = marker[0];
      } else if (marker[0] === fenceMarker && marker.length >= 3) {
        inFence = false;
        lastSafeBoundary = afterNl; // just past a closed fence: always safe
      }
    } else if (!inFence && line.trim() === '') {
      lastSafeBoundary = afterNl; // blank line at top level: safe
    }
    i = afterNl;
  }

  return lastSafeBoundary;
}

function describeOpenFence(text) {
  let inFence = false;
  for (const line of text.split('\n')) {
    if (FENCE_RE.test(line)) inFence = !inFence;
  }
  return inFence;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { splitFinalized, describeOpenFence };
}
