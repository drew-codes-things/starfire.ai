'use strict';

const popupEls = { box: $('composerPopup'), title: $('composerPopupTitle'), list: $('composerPopupList') };
let popupItems = [];
let popupIndex = 0;
let popupTrigger = null;
let documentCache = null;
const SIDE_CHAT_HINT = 'side chat: ask about this chat without adding to it';

function closeComposerPopup() {
  popupEls.box.hidden = true;
  documentCache = null;
  popupItems = [];
  popupTrigger = null;
}

function currentTrigger() {
  const input = els.input;
  const before = input.value.slice(0, input.selectionStart);
  let m = before.match(/^\/([\w-]*)$/);
  if (m) return { char: '/', query: m[1].toLowerCase(), start: 0 };
  m = before.match(/(^|\s)([#@])([^\s#@]*)$/);
  if (m) return { char: m[2], query: m[3].toLowerCase(), start: before.length - m[3].length - 1 };
  return null;
}

async function updateComposerPopup() {
  const trigger = currentTrigger();
  if (!trigger) { closeComposerPopup(); return; }
  popupTrigger = trigger;
  let items = [];
  if (trigger.char === '/') {
    if (!promptCache.length) await loadPrompts();
    popupEls.title.textContent = 'prompts';
    const sideChatItems = sideChatCommandsMatching(trigger.query)
      .map(command => ({ key: '/' + command, sub: SIDE_CHAT_HINT, pick: pickSideChatCommand }));
    const promptItems = promptCache
      .filter(p => p.command.startsWith(trigger.query) || p.title.toLowerCase().includes(trigger.query))
      .map(p => ({ key: '/' + p.command, sub: p.title, pick: () => pickPrompt(p) }));
    if (!promptCache.length) promptItems.push({ key: 'no prompts yet', sub: 'add some in workspace → prompts', pick: () => openSettings('prompts') });
    items = trigger.query ? [...sideChatItems, ...promptItems] : [...promptItems, ...sideChatItems];
  } else if (trigger.char === '#') {
    if (!documentCache) {
      try { ({ documents: documentCache = [] } = await apiJson('/api/documents')); } catch (_) { documentCache = []; }
    }
    popupEls.title.textContent = 'knowledge';
    items = documentCache
      .filter(d => d.filename.toLowerCase().includes(trigger.query))
      .map(d => ({ key: '#' + d.filename, sub: d.chunk_count + ' chunk(s)', pick: () => pickDocument(d) }));
    if (!documentCache.length) items = [{ key: 'no documents yet', sub: 'upload some in workspace → knowledge', pick: () => openSettings('documents') }];
  } else {
    popupEls.title.textContent = 'models';
    items = models
      .filter(m => m.id.toLowerCase().includes(trigger.query))
      .map(m => ({ key: '@' + m.id, sub: window.providerLabel ? window.providerLabel(m.provider) : m.provider, pick: () => pickMention(m) }));
  }
  if (currentTrigger()?.start !== trigger.start) return;
  popupItems = items;
  popupIndex = 0;
  renderComposerPopup();
}

function renderComposerPopup() {
  popupEls.list.innerHTML = '';
  if (!popupItems.length) { closeComposerPopup(); return; }
  popupItems.forEach((item, idx) => {
    const li = document.createElement('li');
    li.setAttribute('role', 'option');
    li.className = idx === popupIndex ? 'active' : '';
    li.innerHTML = `<span class="popup-key">${escapeHtml(item.key)}</span><span class="popup-sub">${escapeHtml(item.sub || '')}</span>`;
    li.onmousedown = e => { e.preventDefault(); item.pick(); };
    popupEls.list.appendChild(li);
  });
  popupEls.box.hidden = false;
}

function removeTriggerText() {
  const input = els.input;
  const end = input.selectionStart;
  const start = popupTrigger ? popupTrigger.start : end;
  input.value = input.value.slice(0, start) + input.value.slice(end);
  input.setSelectionRange(start, start);
}

async function pickPrompt(prompt) {
  closeComposerPopup();
  els.input.value = await expandPromptVariables(prompt.content);
  els.input.dispatchEvent(new Event('input'));
  els.input.focus();
}

function pickSideChatCommand() {
  closeComposerPopup();
  els.input.value = '';
  startSideChat('');
  els.input.focus();
}

function pickDocument(doc) {
  removeTriggerText();
  closeComposerPopup();
  attachKnowledge(doc);
  els.input.focus();
}

function pickMention(model) {
  removeTriggerText();
  closeComposerPopup();
  mentionModel = model;
  renderAttachments();
  els.input.focus();
}

els.input.addEventListener('input', () => { updateComposerPopup(); });
els.input.addEventListener('blur', () => { setTimeout(closeComposerPopup, 150); });

els.form.addEventListener('keydown', e => {
  if (popupEls.box.hidden || !popupItems.length) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    e.stopPropagation();
    popupIndex = (popupIndex + (e.key === 'ArrowDown' ? 1 : -1) + popupItems.length) % popupItems.length;
    renderComposerPopup();
  } else if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault();
    e.stopPropagation();
    popupItems[popupIndex].pick();
  } else if (e.key === 'Escape') {
    e.stopPropagation();
    closeComposerPopup();
  }
}, true);

const SUGGESTIONS = [
  { title: 'Explain a concept', sub: 'like I\'m new to it', text: 'Explain how DNS resolution works, step by step, like I\'m new to networking.' },
  { title: 'Write some code', sub: 'with tests', text: 'Write a Python function that de-duplicates a list while keeping order, with a few pytest tests.' },
  { title: 'Summarise text', sub: 'paste or attach it', text: 'Summarise the following in five bullet points:\n\n' },
  { title: 'Plan something', sub: 'a week, a trip, a project', text: 'Help me plan a realistic week: I want to fit in three workouts and two evenings of side-project work.' },
];

window.renderSuggestions = container => {
  if (!container) return;
  container.innerHTML = '';
  if (!prefs.showSuggestions || !models.length) return;
  for (const s of SUGGESTIONS) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'suggestion';
    btn.innerHTML = `<span class="suggestion-title">${escapeHtml(s.title)}</span><span class="suggestion-sub">${escapeHtml(s.sub)}</span>`;
    btn.onclick = () => {
      els.input.value = s.text;
      els.input.dispatchEvent(new Event('input'));
      els.input.focus();
      els.input.setSelectionRange(s.text.length, s.text.length);
    };
    container.appendChild(btn);
  }
};
