// streamingRenderer.js
//
// Owns the incremental DOM for one streaming assistant message: text arriving
// before the last "safe" boundary (see streamingSegmenter.js) is rendered
// once and frozen into a static block; only the live tail is re-rendered on
// every token. Simplified reimplementation of odysseus-dev's
// static/js/streamingRenderer.js — see streamingSegmenter.js for why this
// isn't a byte-for-byte port.
//
// Usage:
//   const renderer = createStreamRenderer(el, { render: markdownRender });
//   renderer.push(deltaText);   // call once per streamed chunk
//   renderer.finish();          // call once the stream is done — renders any
//                                // remaining tail and highlights code blocks

function createStreamRenderer(el, { render }) {
  let full = '';
  let frozenUpTo = 0;
  let frozenHtml = '';

  function paint() {
    const tail = full.slice(frozenUpTo);
    let tailHtml;
    try {
      tailHtml = render(tail);
    } catch (_) {
      tailHtml = escapeHtml(tail);
    }
    el.innerHTML = frozenHtml + tailHtml;
  }

  function push(delta) {
    full += delta;
    const cut = splitFinalized(full);
    if (cut > frozenUpTo) {
      try {
        frozenHtml += render(full.slice(frozenUpTo, cut));
        frozenUpTo = cut;
      } catch (_) {
        // Renderer choked on a boundary we thought was safe — fall back to a
        // full re-render on finish() rather than freezing bad HTML.
      }
    }
    paint();
  }

  function finish() {
    try {
      frozenHtml = render(full);
    } catch (_) {
      frozenHtml = escapeHtml(full);
    }
    frozenUpTo = full.length;
    el.innerHTML = frozenHtml;
    el.querySelectorAll('pre code').forEach(block => {
      if (window.hljs) window.hljs.highlightElement(block);
    });
  }

  function text() {
    return full;
  }

  return { push, finish, text };
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}
