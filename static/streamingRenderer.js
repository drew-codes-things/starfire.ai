

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
