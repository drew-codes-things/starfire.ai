const FENCE_RE = /^ {0,3}(`{3,}|~{3,})/;

function splitFinalized(text) {
  let inFence = false;
  let fenceMarker = '';
  let lastSafeBoundary = 0;
  let i = 0;
  const n = text.length;

  while (i < n) {
    const nl = text.indexOf('\n', i);
    if (nl === -1) break;
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
        lastSafeBoundary = afterNl;
      }
    } else if (!inFence && line.trim() === '') {
      lastSafeBoundary = afterNl;
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
