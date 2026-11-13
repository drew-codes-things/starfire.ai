'use strict';

const PREF_DEFAULTS = {
  wideChat: false,
  autoTitle: true,
  autoTags: true,
  followUps: true,
  ctrlEnterSends: false,
  autoSpeak: false,
  showSuggestions: true,
  userBubbles: true,
  checkCode: true,
};

const prefs = { ...PREF_DEFAULTS };
try { Object.assign(prefs, JSON.parse(localStorage.getItem('sf_prefs') || '{}')); } catch (_) {}

function savePrefs() {
  try { localStorage.setItem('sf_prefs', JSON.stringify(prefs)); } catch (_) {}
}

const MOD_KEY_LABEL = /Mac|iPhone|iPad/.test(navigator.platform || '') ? '⌘' : 'Ctrl';

function applyPrefs() {
  document.documentElement.classList.toggle('wide-chat', !!prefs.wideChat);
  const sendKeys = prefs.ctrlEnterSends ? `<kbd>${MOD_KEY_LABEL}</kbd><kbd>Enter</kbd>` : '<kbd>Enter</kbd>';
  const newlineKeys = prefs.ctrlEnterSends ? '<kbd>Enter</kbd>' : '<kbd>Shift</kbd><kbd>Enter</kbd>';
  document.getElementById('shortcutSendKeys').innerHTML = sendKeys;
  document.getElementById('shortcutNewlineKeys').innerHTML = newlineKeys;
  document.getElementById('composerHint').textContent = (prefs.ctrlEnterSends
    ? `${MOD_KEY_LABEL}+Enter to send · Enter for a new line`
    : 'Enter to send · Shift+Enter for a new line') + ' · / prompts · # knowledge · @ model';
}

document.querySelectorAll('kbd[data-mod]').forEach(key => { key.textContent = MOD_KEY_LABEL; });

document.querySelectorAll('[data-pref]').forEach(input => {
  input.checked = !!prefs[input.dataset.pref];
  input.addEventListener('change', () => {
    prefs[input.dataset.pref] = input.checked;
    savePrefs();
    applyPrefs();
    rerenderInPlace();
  });
});
applyPrefs();

const NARROW_SCREEN_PX = 860;
const SEARCH_DEBOUNCE_MS = 250;
const MAX_ATTACHMENT_CHARS = 60000;
const SCROLL_BUTTON_THRESHOLD_PX = 300;

function isNarrowScreen() { return window.innerWidth <= NARROW_SCREEN_PX; }

async function apiJson(url, options = {}) {
  const init = { ...options };
  if (init.body !== undefined && typeof init.body !== 'string' && !(init.body instanceof FormData)) {
    init.body = JSON.stringify(init.body);
    init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) };
  }
  const r = await fetch(url, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || 'server error ' + r.status);
  return data;
}

function closeAllMenus(except) {
  document.querySelectorAll('.menu, .model-menu, .context-menu').forEach(m => {
    if (m !== except) m.hidden = true;
  });
  $('modelPickerBtn').setAttribute('aria-expanded', String(!$('modelMenu').hidden));
  $('accountMenuBtn').setAttribute('aria-expanded', String(!$('accountMenu').hidden));
  $('plusBtn').setAttribute('aria-expanded', String(!$('plusMenu').hidden));
}

document.addEventListener('click', e => {
  if (!e.target.closest('.menu-wrap, .model-picker, .context-menu, .chat-item-menu-btn, .folder-menu-btn')) closeAllMenus();
});

const dialogEls = {
  modal: $('dialogModal'), title: $('dialogTitle'), text: $('dialogText'),
  input: $('dialogInput'), ok: $('dialogOkBtn'), cancel: $('dialogCancelBtn'),
};

function showDialog({ title, text = '', input, okLabel = 'ok', danger = false, allowEmpty = false, inputType = 'text', placeholder = '' }) {
  return new Promise(resolve => {
    dialogEls.title.textContent = title;
    dialogEls.text.textContent = text;
    dialogEls.input.hidden = input === undefined;
    dialogEls.input.type = inputType;
    dialogEls.input.placeholder = placeholder;
    dialogEls.input.value = input || '';
    dialogEls.ok.textContent = okLabel;
    dialogEls.ok.className = danger ? 'btn danger-fill' : 'btn send';
    dialogEls.modal.hidden = false;
    (input === undefined ? dialogEls.ok : dialogEls.input).focus();

    const finish = value => {
      dialogEls.modal.hidden = true;
      dialogEls.ok.onclick = dialogEls.cancel.onclick = dialogEls.input.onkeydown = dialogEls.modal.onkeydown = null;
      resolve(value);
    };
    dialogEls.ok.onclick = () => {
      if (input === undefined) { finish(true); return; }
      const typed = dialogEls.input.value.trim();
      finish(typed || (allowEmpty ? '' : null));
    };
    dialogEls.cancel.onclick = () => finish(null);
    dialogEls.input.onkeydown = submitOnEnter(() => dialogEls.ok.click());
    dialogEls.modal.onkeydown = closeOnEscape(() => finish(null));
  });
}
