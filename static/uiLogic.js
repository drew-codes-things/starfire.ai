'use strict';

const DAY_MS = 86400000;
const TOAST_MS = 2800;
const TOAST_ERROR_MS = 5000;
const TOAST_FADE_MS = 220;
const THEME_CHOICES = ['system', 'light', 'dark'];
const CHAT_TITLE_CHARS = 60;
const UNTITLED_CHAT = 'New chat';

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

const SHELL_TOOL = 'run_shell';
const UNTRUSTED_APPROVAL_NOTE = 'Untrusted content (a web page, search result, email, document or tool result) ' +
  'was read in this turn and may be trying to steer the model. Check this carefully before you approve it.';
const UNSANDBOXED_SHELL_NOTE = 'This command runs without a sandbox: bubblewrap is not installed or cannot start on ' +
  'this server, so the command can read and change every file the server can.';

function shellApprovalNotes(data) {
  const notes = [];
  if (data && data.untrusted === true) notes.push(UNTRUSTED_APPROVAL_NOTE);
  if (data && data.unsandboxed === true) notes.push(UNSANDBOXED_SHELL_NOTE);
  return notes;
}

function pendingApprovalCards(commands, edits) {
  const isWaiting = item => item && typeof item.id === 'string' && item.id;
  const shellCards = (Array.isArray(commands) ? commands : []).filter(isWaiting).map(item => ({
    kind: 'shell', id: item.id,
    data: {
      pending_id: item.id, command: item.command || '', cwd: item.cwd || '',
      tool: typeof item.tool === 'string' && item.tool ? item.tool : SHELL_TOOL, untrusted: item.untrusted === true,
      unsandboxed: item.unsandboxed === true,
    },
  }));
  const editCards = (Array.isArray(edits) ? edits : []).filter(isWaiting).map(item => ({
    kind: 'edit', id: item.id,
    data: { staged: true, pending_id: item.id, path: item.path || '', diff: item.diff || '', untrusted: item.untrusted === true },
  }));
  return [...shellCards, ...editCards];
}

const CARD_HEADING_FIELDS = ['action', 'to', 'account_id'];
const CARD_HEADING_VALUE_CHARS = 200;
const TOP_LEVEL_CARD_FIELD = /^ {2}"([^"\\]+)": (.*)$/;
const NESTED_CARD_LINE = /^ {2}/;

function topLevelCallFields(command) {
  const fields = {};
  let current = null;
  for (const line of String(command || '').split('\n')) {
    const field = TOP_LEVEL_CARD_FIELD.exec(line);
    if (field) {
      current = field[1];
      fields[current] = field[2];
    } else if (current && NESTED_CARD_LINE.test(line)) {
      fields[current] += ' ' + line.trim();
    } else {
      current = null;
    }
  }
  return fields;
}

function shortCardValue(value) {
  const shown = value.replace(/,$/, '').replace(/\s+/g, ' ');
  if (shown.length <= CARD_HEADING_VALUE_CHARS) return shown;
  const rest = shown.length - CARD_HEADING_VALUE_CHARS;
  return `${shown.slice(0, CARD_HEADING_VALUE_CHARS)}… (${rest} more characters, see the full call below)`;
}

function approvalCallDetails(command) {
  const fields = topLevelCallFields(command);
  const named = CARD_HEADING_FIELDS.filter(name => Object.hasOwn(fields, name));
  if (!named.length) return '';
  return ` (${named.map(name => `${name}: ${shortCardValue(fields[name])}`).join(', ')})`;
}

function approvalHeading(data) {
  const tool = data && typeof data.tool === 'string' && data.tool ? data.tool : SHELL_TOOL;
  if (tool === SHELL_TOOL) return 'shell command - waiting for your approval';
  const details = approvalCallDetails(data.command);
  return `${tool.replace(/^mcp__[^_]+__/, '')}${details} - waiting for your approval to run with these details`;
}

function resolveChatControls(session, defaults) {
  const ownPrompt = session && typeof session.system_prompt === 'string';
  const ownParams = !!session && !!session.params && typeof session.params === 'object';
  return {
    system_prompt: ownPrompt ? session.system_prompt : defaults.system_prompt,
    params: { ...(ownParams ? session.params : defaults.params) },
  };
}

function submitOnEnter(submit) {
  return event => {
    if (event.key !== 'Enter' || event.isComposing) return;
    event.preventDefault();
    submit();
  };
}

function closeOnEscape(close) {
  return event => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    close();
  };
}

function chatTitleFrom(messages, textBeingSent = '') {
  const first = messages.find(message => message.role === 'user');
  const shown = first ? (first.display ?? first.content) : '';
  return (shown || textBeingSent || '').trim().slice(0, CHAT_TITLE_CHARS) || UNTITLED_CHAT;
}

function firstUnusedModel(models, usedKeys, keyOf) {
  const used = new Set(usedKeys);
  return models.find(model => !used.has(keyOf(model))) || models[0] || null;
}

function isLastAssistantMessage(messages, index) {
  if (!messages[index] || messages[index].role !== 'assistant') return false;
  return !messages.slice(index + 1).some(message => message.role === 'assistant');
}

const SPOKEN_SENTENCE = /[^.!?\n]*(?:[.!?]+["'\u201d\u2019)\]]*\s+|\n+)/g;
const MAX_SPOKEN_CHARS = 500;

function takeSpokenSentences(text) {
  const sentences = [];
  let used = 0;
  for (const match of text.matchAll(SPOKEN_SENTENCE)) {
    if (match[0].trim()) sentences.push(match[0].trim());
    used = match.index + match[0].length;
  }
  return { sentences, rest: text.slice(used) };
}

function speakableText(text) {
  return text.replace(/[*_#`~>]+/g, ' ').replace(/\s+/g, ' ').trim();
}

function spokenPieces(sentence, limit = MAX_SPOKEN_CHARS) {
  const pieces = [];
  let rest = sentence;
  while (rest.length > limit) {
    const cut = rest.lastIndexOf(' ', limit);
    const at = cut > 0 ? cut : limit;
    pieces.push(rest.slice(0, at).trim());
    rest = rest.slice(at).trim();
  }
  if (rest) pieces.push(rest);
  return pieces;
}

const REGENERATE_WITH = 'Regenerate with…';

function regenerateChoice(option) {
  if (!option || !option.value) return null;
  return { id: option.value, endpoint_id: option.dataset.endpointId };
}

const MENU_STEPS = { ArrowDown: 1, ArrowUp: -1 };

function menuFocusIndex(key, current, count) {
  if (!count) return -1;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  if (!Object.hasOwn(MENU_STEPS, key)) return -1;
  if (current < 0) return MENU_STEPS[key] > 0 ? 0 : count - 1;
  return (current + MENU_STEPS[key] + count) % count;
}

function menuKeyHandler(getItems, close, getFocused = () => document.activeElement) {
  return event => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
      return;
    }
    const items = getItems();
    const next = menuFocusIndex(event.key, items.indexOf(getFocused()), items.length);
    if (next < 0) return;
    event.preventDefault();
    items[next].focus();
  };
}

const LIST_LOADING_ROW = '<li class="settings-hint" role="status">loading…</li>';

function showListLoading(list) {
  if (!list || list.children.length) return false;
  list.innerHTML = LIST_LOADING_ROW;
  return true;
}

async function failureMessage(response, fallback) {
  const body = await response.json().catch(() => ({}));
  return body && typeof body.detail === 'string' && body.detail ? body.detail : fallback;
}

const NO_EMAIL_ACCOUNTS = 'No email accounts yet, add one above';

function ruleAccountChoices(accounts) {
  const list = Array.isArray(accounts) ? accounts : [];
  if (!list.length) return { usable: false, options: [{ value: '', label: NO_EMAIL_ACCOUNTS }] };
  return { usable: true, options: list.map(account => ({ value: account.id, label: account.label })) };
}

const BACKEND_RUNS_ON_ITS_OWN = 'It runs on its own: starfire only starts Ollama, and only when Ollama is installed here.';
const NO_LOCAL_OLLAMA = 'There is no Ollama here to start. ArtyEngine and other Ollama-compatible servers run on their own: '
  + 'start yours and set OLLAMA_BASE_URL to its address.';
const FOUND_NOT_ADDED = 'starfire only sends work to a model server you have added: add it under Connections '
  + '(add ollama or scan network) to use it.';

function scanEndpointBody(item) {
  return { base_url: item.base_url, kind: item.kind || 'ollama', label: item.label };
}

const OPENAI_ONLY_NOTE = 'OpenAI-compatible, not used for memory search or pulls';
const ALREADY_ADDED = 409;

function scanSummary(results) {
  const added = results.filter(result => result.ok)
    .map(result => (result.kind === 'openai-compatible' ? `${result.label} (${OPENAI_ONLY_NOTE})` : result.label));
  const already = results.filter(result => !result.ok && result.status === ALREADY_ADDED).map(result => result.label);
  const failed = results.filter(result => !result.ok && result.status !== ALREADY_ADDED)
    .map(result => `${result.label}: ${result.detail || 'add failed'}`);
  const parts = [];
  if (added.length) parts.push(`added ${added.join(', ')}`);
  if (already.length) parts.push(`already added ${already.join(', ')}`);
  if (failed.length) parts.push(`not added ${failed.join(', ')}`);
  return { text: parts.join('; '), ok: failed.length === 0 };
}

function addedLocalMessage(endpoint) {
  if (endpoint.kind !== 'openai-compatible') return 'added';
  return `added ${endpoint.base_url} as an OpenAI-compatible server: it has no Ollama API, so it is used for chat `
    + 'but not for memory search or pulls';
}

function backendStatusPlan(status) {
  const backend = status.backend || 'Ollama';
  if (status.running && status.in_use === false) {
    return { headline: `Found ${backend} at ${status.found_at}, not added.`, note: FOUND_NOT_ADDED,
      offerStart: false, showInstall: false };
  }
  if (status.running) {
    return { headline: `✓ ${backend} is running.`, note: status.installed ? '' : BACKEND_RUNS_ON_ITS_OWN,
      offerStart: false, showInstall: false };
  }
  if (status.installed) {
    return { headline: 'Installed, but not running.', note: '', offerStart: true, showInstall: false };
  }
  return { headline: 'Ollama is not installed.', note: NO_LOCAL_OLLAMA, offerStart: false, showInstall: true };
}

const TOOL_ERROR_PREFIX = 'error:';
const TOOL_REASON_CHARS = 200;
const BLOCKED_TOOL_REASONS = [
  /the user rejected/, /did not approve/, /nobody is there to ask/, /only an admin account can use/,
  /can only change files/, /does not run 'gh/, /is not enabled for this chat/, /is not a public mail server/,
];

function shortToolName(name) {
  return String(name || 'tool').replace(/^mcp__[^_]+__/, '');
}

function toolOutcome(result) {
  const text = typeof result === 'string' ? result.trim() : '';
  if (!text.startsWith(TOOL_ERROR_PREFIX)) return { state: 'done', reason: '' };
  const reason = text.slice(TOOL_ERROR_PREFIX.length).trim();
  const blocked = BLOCKED_TOOL_REASONS.some(pattern => pattern.test(reason));
  return { state: blocked ? 'blocked' : 'failed', reason };
}

function toolOutcomeLine(name, result) {
  const { state, reason } = toolOutcome(result);
  const tool = shortToolName(name);
  if (state === 'done') return { text: `» ${tool} done`, cls: 'info' };
  const shown = reason.length > TOOL_REASON_CHARS ? reason.slice(0, TOOL_REASON_CHARS) + '…' : reason;
  return { text: `» ${tool} ${state}: ${shown}`, cls: 'err' };
}

function modelPatternsFrom(text) {
  const patterns = String(text || '').split(/[,\n]/).map(p => p.trim()).filter(Boolean);
  return patterns.length ? patterns : null;
}

function modelAccessHint(restricted) {
  const hidden = (restricted || []).join(', ') || 'none';
  return 'Model names or patterns (* matches anything), separated by commas. Only these can be listed or used. ' +
    'A restricted model is only opened by its full name here, never by a pattern or a name without its tag. ' +
    `Leave it blank for every model except the restricted ones (${hidden}). Admins can always use every model.`;
}

function modelAccessSummary(models) {
  return models === null ? 'every model except the restricted ones' : (models.join(', ') || 'no models');
}

const LOCAL_MEDIA_PROTOCOLS = ['data:', 'blob:'];
const CODE_LANGUAGE_CLASS = /^language-[\w+#.-]+$/;

function isLocalMediaUrl(src, origin) {
  let url;
  try {
    url = new URL(String(src), origin);
  } catch (_) {
    return false;
  }
  return LOCAL_MEDIA_PROTOCOLS.includes(url.protocol) || url.origin === origin;
}

function isCodeLanguageClass(value) {
  const names = String(value).split(/\s+/).filter(Boolean);
  return names.length > 0 && names.every(name => CODE_LANGUAGE_CLASS.test(name));
}

const AUTO_MODEL_ID = 'auto';
const AUTO_MODEL_NAME = 'Arty Auto';
const AUTO_MODEL_HINT = 'picks the best model for each message';
const AUTO_CHOICE = Object.freeze({ endpoint_id: AUTO_MODEL_ID, model: AUTO_MODEL_ID });

function isAutoChoice(choice) {
  return !!choice && choice.endpoint_id === AUTO_MODEL_ID && (choice.model ?? choice.id) === AUTO_MODEL_ID;
}

function isListedChoice(choice, listed) {
  const name = choice && (choice.model ?? choice.id);
  return !!name && listed.some(m => m.id === name && m.endpoint_id === choice.endpoint_id);
}

function modelChoiceToShow(chat, accountChoice, listed) {
  for (const choice of [chat, accountChoice]) {
    if (isAutoChoice(choice)) return AUTO_CHOICE;
    if (isListedChoice(choice, listed)) return { endpoint_id: choice.endpoint_id, model: choice.model ?? choice.id };
  }
  return AUTO_CHOICE;
}

function autoPickLabel(modelName, role) {
  return role ? `${modelName} \u00b7 ${AUTO_MODEL_NAME}: ${role}` : modelName;
}

function replyModelLabel(message) {
  return autoPickLabel(message.model || '', message.auto);
}

const PICKER_MODE_AUTO = 'auto';
const PICKER_MODE_CHOSEN = 'chosen';

function autoModelEntry() {
  return { id: AUTO_MODEL_ID, endpoint_id: AUTO_MODEL_ID, provider: AUTO_MODEL_ID };
}

function pickerMode(current) {
  return !current || isAutoChoice(current) ? PICKER_MODE_AUTO : PICKER_MODE_CHOSEN;
}

function autoMatchesSearch(query) {
  return !query || AUTO_MODEL_NAME.toLowerCase().includes(query) || AUTO_MODEL_HINT.includes(query);
}

function modelMatchesSearch(model, query) {
  return !query || model.id.toLowerCase().includes(query) || (model.provider || '').toLowerCase().includes(query);
}

function artyServers(listed) {
  return new Set(listed.filter(m => m.own_tools === true).map(m => m.endpoint_id));
}

function nameAfterServerPrefix(id) {
  const cut = id.indexOf('/');
  return cut > 0 ? id.slice(cut + 1) : '';
}

function isArtyRouteToADirectModel(model, listed, servedByArty) {
  if (!servedByArty.has(model.endpoint_id)) return false;
  const names = [model.id, nameAfterServerPrefix(model.id)].filter(Boolean);
  return listed.some(other => !servedByArty.has(other.endpoint_id) && names.includes(other.id));
}

function serverNumbers(listed) {
  const numbers = new Map();
  for (const m of listed) if (!numbers.has(m.endpoint_id)) numbers.set(m.endpoint_id, numbers.size + 1);
  return numbers;
}

function distinctModels(listed) {
  const servedByArty = artyServers(listed);
  const kept = listed.filter(m => !isArtyRouteToADirectModel(m, listed, servedByArty));
  const numbers = serverNumbers(listed);
  const serversPerName = new Map();
  for (const m of kept) serversPerName.set(m.id, (serversPerName.get(m.id) || 0) + 1);
  return kept.map(m => (serversPerName.get(m.id) > 1 ? { ...m, server_note: `server ${numbers.get(m.endpoint_id)}` } : m));
}

function modelMenuEntries(listed, query) {
  const search = (query || '').trim().toLowerCase();
  const autoShown = autoMatchesSearch(search) ? [autoModelEntry()] : [];
  return [...autoShown, ...distinctModels(listed).filter(m => modelMatchesSearch(m, search))];
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    isLocalMediaUrl, isCodeLanguageClass, toolOutcome, toolOutcomeLine,
    modelPatternsFrom, modelAccessHint, modelAccessSummary,
    DAY_MS, TOAST_MS, TOAST_ERROR_MS, TOAST_FADE_MS,
    sentenceCase, dateGroupLabel, themeChoiceFrom, themeIsLight, showToast, removalQuestion, confirmRemove,
    pendingApprovalCards, UNTRUSTED_APPROVAL_NOTE, UNSANDBOXED_SHELL_NOTE, shellApprovalNotes, approvalHeading, resolveChatControls, isLastAssistantMessage,
    CHAT_TITLE_CHARS, submitOnEnter, closeOnEscape, chatTitleFrom, firstUnusedModel,
    MAX_SPOKEN_CHARS, takeSpokenSentences, speakableText, spokenPieces,
    REGENERATE_WITH, regenerateChoice,
    menuFocusIndex, menuKeyHandler,
    showListLoading,
    failureMessage,
    NO_EMAIL_ACCOUNTS, ruleAccountChoices,
    AUTO_MODEL_ID, AUTO_MODEL_NAME, AUTO_MODEL_HINT, AUTO_CHOICE, isAutoChoice, modelChoiceToShow, autoPickLabel, replyModelLabel,
    autoModelEntry, PICKER_MODE_AUTO, PICKER_MODE_CHOSEN, pickerMode, distinctModels, modelMenuEntries,
    BACKEND_RUNS_ON_ITS_OWN, NO_LOCAL_OLLAMA, FOUND_NOT_ADDED, backendStatusPlan, scanEndpointBody, scanSummary, addedLocalMessage,
  };
}
