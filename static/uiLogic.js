'use strict';

const DAY_MS = 86400000;
const TOAST_MS = 2800;
const TOAST_ERROR_MS = 5000;
const TOAST_FADE_MS = 220;
const THEME_CHOICES = ['system', 'light', 'dark'];

function sentenceCase(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function dateGroupLabel(iso, now = new Date()) {
  const updated = new Date(iso);
  if (isNaN(updated)) return 'Older';
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const age = startOfToday - updated;
  if (age <= 0) return 'Today';
  if (age <= DAY_MS) return 'Yesterday';
  if (age <= 7 * DAY_MS) return 'Previous 7 days';
  if (age <= 30 * DAY_MS) return 'Previous 30 days';
  return updated.toLocaleString(undefined, { month: 'long', year: 'numeric' });
}

function themeChoiceFrom(readSetting) {
  const saved = readSetting('sf_theme');
  if (THEME_CHOICES.includes(saved)) return saved;
  return readSetting('sf_light') === '1' ? 'light' : 'dark';
}

function themeIsLight(choice, systemPrefersLight) {
  return choice === 'light' || (choice === 'system' && systemPrefersLight);
}

function showToast(message, kind = 'info') {
  const box = document.getElementById('toasts');
  if (!box || !message) return null;
  const isError = kind === 'error';
  const toast = document.createElement('div');
  toast.className = 'toast' + (isError ? ' error' : '');
  toast.setAttribute('role', isError ? 'alert' : 'status');
  toast.textContent = message;
  box.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('leaving');
    setTimeout(() => toast.remove(), TOAST_FADE_MS);
  }, isError ? TOAST_ERROR_MS : TOAST_MS);
  return toast;
}

function removalQuestion(what) {
  return { title: `Remove ${what}?`, text: 'This cannot be undone.', okLabel: 'Remove', danger: true };
}

async function confirmRemove(what, ask = showDialog) {
  return !!(await ask(removalQuestion(what)));
}

function pendingApprovalCards(commands, edits) {
  const isWaiting = item => item && typeof item.id === 'string' && item.id;
  const shellCards = (Array.isArray(commands) ? commands : []).filter(isWaiting).map(item => ({
    kind: 'shell', id: item.id,
    data: { pending_id: item.id, command: item.command || '', cwd: item.cwd || '' },
  }));
  const editCards = (Array.isArray(edits) ? edits : []).filter(isWaiting).map(item => ({
    kind: 'edit', id: item.id,
    data: { staged: true, pending_id: item.id, path: item.path || '', diff: item.diff || '' },
  }));
  return [...shellCards, ...editCards];
}

function resolveChatControls(session, defaults) {
  const ownPrompt = session && typeof session.system_prompt === 'string';
  const ownParams = !!session && !!session.params && typeof session.params === 'object';
  return {
    system_prompt: ownPrompt ? session.system_prompt : defaults.system_prompt,
    params: { ...(ownParams ? session.params : defaults.params) },
  };
}

function isLastAssistantMessage(messages, index) {
  if (!messages[index] || messages[index].role !== 'assistant') return false;
  return !messages.slice(index + 1).some(message => message.role === 'assistant');
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    DAY_MS, TOAST_MS, TOAST_ERROR_MS, TOAST_FADE_MS,
    sentenceCase, dateGroupLabel, themeChoiceFrom, themeIsLight, showToast, removalQuestion, confirmRemove,
    pendingApprovalCards, resolveChatControls, isLastAssistantMessage,
  };
}
