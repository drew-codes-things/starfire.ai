'use strict';

function closeTopmostLayer() {
  if (!paletteEls.overlay.hidden) { closePalette(); return true; }
  if (!$('dialogModal').hidden) return false;
  if (document.querySelector('.menu:not([hidden]), .model-menu:not([hidden]), .context-menu:not([hidden])')) {
    closeAllMenus();
    return true;
  }
  if (!$('shortcutsModal').hidden) { closeShortcuts(); return true; }
  if (!compareEls.modal.hidden) { closeCompare(); return true; }
  if (!archivedEls.modal.hidden) { archivedEls.modal.hidden = true; return true; }
  if (!settingsEls.modal.hidden) { closeSettings(); return true; }
  if (!$('controlsPanel').hidden) { toggleControls(false); return true; }
  return false;
}

document.addEventListener('keydown', e => {
  const key = e.key.toLowerCase();
  if ((e.metaKey || e.ctrlKey) && key === 'k') {
    e.preventDefault();
    if (paletteEls.overlay.hidden) openPalette(); else closePalette();
  } else if ((e.metaKey || e.ctrlKey) && key === '/') {
    e.preventDefault();
    if ($('shortcutsModal').hidden) openShortcuts(); else closeShortcuts();
  } else if (key === 'escape' && !e.shiftKey && !e.defaultPrevented) {
    if (closeTopmostLayer()) {
      e.preventDefault();
    } else if (busy && abortController && $('dialogModal').hidden) {
      e.preventDefault();
      abortController.abort();
    }
  }
});

const LAYER_SELECTOR = '.palette-overlay:not([hidden]), .modal-overlay:not([hidden])';
const FOCUSABLE_SELECTOR = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';
const LAYER_ORDER = ['settingsModal', 'archivedModal', 'compareModal', 'shortcutsModal', 'dialogModal', 'paletteOverlay'];
let focusBeforeLayers = null;
const focusInsideLayer = {};

function topLayer() {
  const open = [...document.querySelectorAll(LAYER_SELECTOR)];
  open.sort((x, y) => LAYER_ORDER.indexOf(x.id) - LAYER_ORDER.indexOf(y.id));
  return open[open.length - 1] || null;
}

function focusableIn(root) {
  return [...root.querySelectorAll(FOCUSABLE_SELECTOR)].filter(el => !el.disabled && el.offsetParent !== null);
}

document.addEventListener('focusin', e => {
  const layer = e.target.closest ? e.target.closest('.palette-overlay, .modal-overlay') : null;
  if (layer) focusInsideLayer[layer.id] = e.target;
  else focusBeforeLayers = e.target;
});

document.addEventListener('keydown', e => {
  if (e.key !== 'Tab') return;
  const layer = topLayer();
  if (!layer) return;
  const items = focusableIn(layer);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (!layer.contains(document.activeElement)) {
    e.preventDefault();
    first.focus();
  } else if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
});

function returnFocusAfterClose(closed) {
  delete focusInsideLayer[closed.id];
  if (closed.contains(document.activeElement) === false && document.activeElement !== document.body) return;
  const layer = topLayer();
  const remembered = layer ? (focusInsideLayer[layer.id] || focusableIn(layer)[0]) : focusBeforeLayers;
  const isVisible = el => !!el && el.isConnected && el.offsetParent !== null;
  const menuOpener = remembered && remembered.closest ? remembered.closest('.menu-wrap') : null;
  const target = [remembered, menuOpener && menuOpener.querySelector('button'), els.input].find(isVisible);
  if (target) target.focus();
}

const layerObserver = new MutationObserver(records => {
  for (const record of records) {
    if (record.target.hidden) returnFocusAfterClose(record.target);
  }
});
document.querySelectorAll('.palette-overlay, .modal-overlay').forEach(layer => {
  layerObserver.observe(layer, { attributes: true, attributeFilter: ['hidden'] });
});

const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
if (SpeechRecognitionCtor && els.micBtn) {
  const recognition = new SpeechRecognitionCtor();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = 'en-US';
  let recognizing = false;

  recognition.onresult = e => {
    const transcript = e.results[0][0].transcript;
    els.input.value += (els.input.value && !els.input.value.endsWith(' ') ? ' ' : '') + transcript;
    els.input.dispatchEvent(new Event('input'));
  };
  recognition.onend = () => { recognizing = false; els.micBtn.classList.remove('recording'); };
  recognition.onerror = () => { recognizing = false; els.micBtn.classList.remove('recording'); };

  els.micBtn.hidden = false;
  els.micBtn.onclick = () => {
    if (recognizing) { recognition.stop(); return; }
    recognizing = true;
    els.micBtn.classList.add('recording');
    try { recognition.start(); } catch (_) { recognizing = false; els.micBtn.classList.remove('recording'); }
  };
}

const THEME_FADE_MS = 260;
const systemPrefersLight = window.matchMedia('(prefers-color-scheme: light)');
const themeInputs = document.querySelectorAll('input[name="themeChoice"]');

function storedTheme() {
  try {
    return themeChoiceFrom(key => localStorage.getItem(key));
  } catch (_) {
    return 'dark';
  }
}

function isLightTheme(choice) {
  return themeIsLight(choice, systemPrefersLight.matches);
}

function applyTheme(choice, animate = false) {
  const root = document.documentElement;
  if (animate) {
    root.classList.add('theme-fade');
    setTimeout(() => root.classList.remove('theme-fade'), THEME_FADE_MS);
  }
  root.classList.toggle('light', isLightTheme(choice));
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', getComputedStyle(root).getPropertyValue('--bg').trim());
  themeInputs.forEach(input => { input.checked = input.value === choice; });
}

function setTheme(choice) {
  try {
    localStorage.setItem('sf_theme', choice);
    localStorage.setItem('sf_light', choice === 'light' ? '1' : '0');
  } catch (_) {}
  applyTheme(choice, true);
}

function toggleLightDark() {
  setTheme(isLightTheme(storedTheme()) ? 'dark' : 'light');
}

applyTheme(storedTheme());
themeInputs.forEach(input => {
  input.addEventListener('change', () => { if (input.checked) setTheme(input.value); });
});
systemPrefersLight.addEventListener('change', () => {
  if (storedTheme() === 'system') applyTheme('system', true);
});

loadAppConfig().then(() => loadModels()).then(() => restoreSession()).then(() => {
  checkModelToolCapability();
  restorePendingApprovals();
});

setUpLogout().catch(error => console.error('starfire.ai: could not check the login', error));
