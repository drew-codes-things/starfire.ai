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

const STAMP_LAST_WEEKDAY_NAMED = 6;

function startOfLocalDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function stampDayLabel(moment, now, locale) {
  const daysAgo = Math.round((startOfLocalDay(now) - startOfLocalDay(moment)) / DAY_MS);
  if (daysAgo === 0) return 'Today';
  if (daysAgo === 1) return 'Yesterday';
  if (daysAgo > 1 && daysAgo <= STAMP_LAST_WEEKDAY_NAMED) return moment.toLocaleDateString(locale, { weekday: 'short' });
  const dayAndMonth = { day: 'numeric', month: 'short' };
  if (moment.getFullYear() === now.getFullYear()) return moment.toLocaleDateString(locale, dayAndMonth);
  return moment.toLocaleDateString(locale, { ...dayAndMonth, year: 'numeric' });
}

function messageStamp(iso, now = new Date(), locale = undefined) {
  const moment = new Date(iso);
  if (!iso || isNaN(moment)) return '';
  const clock = moment.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
  return stampDayLabel(moment, now, locale) + ' ' + clock;
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

function modelAccessSummary(models) {
  return models === null ? 'every model except the restricted ones' : (models.join(', ') || 'no models');
}

const NO_CONNECTION_NAME = 'Other';

function groupModelsByConnection(available) {
  const groups = [];
  for (const model of available || []) {
    const name = model.connection || NO_CONNECTION_NAME;
    let group = groups.find(g => g.connection === name && g.endpointId === model.endpoint_id);
    if (!group) {
      group = { connection: name, endpointId: model.endpoint_id, models: [] };
      groups.push(group);
    }
    group.models.push(model);
  }
  return groups;
}

const REMOTE_SECTION = 'Remote';

function splitByRemote(models) {
  const list = models || [];
  return { local: list.filter(m => m.remote !== true), remote: list.filter(m => m.remote === true) };
}

function accessSections(available) {
  const { local, remote } = splitByRemote(available);
  return [{ title: '', groups: groupModelsByConnection(local) }, { title: REMOTE_SECTION, groups: groupModelsByConnection(remote) }]
    .filter(section => section.groups.length);
}

function accessMatchesSearch(model, query) {
  const needle = String(query || '').trim().toLowerCase();
  return !needle || model.id.toLowerCase().includes(needle) || (model.connection || '').toLowerCase().includes(needle);
}

function ticksFromAccess(view) {
  return new Set((view.available || []).filter(m => m.allowed && !m.by_pattern).map(m => m.id));
}

const ACCESS_WILDCARDS = '*?[';
const ESCAPED_ACCESS_WILDCARD = /\[([*?[])\]/g;

function escapeModelName(name) {
  return [...String(name)].map(c => (ACCESS_WILDCARDS.includes(c) ? `[${c}]` : c)).join('');
}

function isAccessPattern(entry) {
  return [...String(entry).replace(ESCAPED_ACCESS_WILDCARD, '')].some(c => ACCESS_WILDCARDS.includes(c));
}

function nameWithoutTag(name) {
  const bare = name.split('@')[0];
  const colon = bare.lastIndexOf(':');
  if (colon <= 0) return bare;
  const tag = bare.slice(colon + 1);
  return /[\\/]/.test(tag) ? bare : bare.slice(0, colon);
}

function escapeRegExpChar(c) {
  return /[\\^$.*+?()[\]{}|/-]/.test(c) ? '\\' + c : c;
}

function characterClassSource(pattern, start) {
  let at = start + 1;
  if (pattern[at] === '!') at++;
  if (pattern[at] === ']') at++;
  while (at < pattern.length && pattern[at] !== ']') at++;
  if (at >= pattern.length) return null;
  let inside = pattern.slice(start + 1, at).replace(/\\/g, '\\\\').replace(/]/g, '\\]');
  if (inside.startsWith('!')) inside = '^' + inside.slice(1);
  else if (inside.startsWith('^')) inside = '\\' + inside;
  return { source: `[${inside}]`, end: at + 1 };
}

function accessPatternRegExp(pattern) {
  let source = '';
  for (let at = 0; at < pattern.length;) {
    const c = pattern[at];
    if (c === '*') { source += '[\\s\\S]*'; at++; continue; }
    if (c === '?') { source += '[\\s\\S]'; at++; continue; }
    const cls = c === '[' ? characterClassSource(pattern, at) : null;
    if (cls) { source += cls.source; at = cls.end; continue; }
    source += escapeRegExpChar(c);
    at++;
  }
  return new RegExp(`^${source}$`);
}

function accessPatternMatches(pattern, modelId) {
  const name = String(modelId).trim().toLowerCase();
  const matcher = accessPatternRegExp(String(pattern).trim().toLowerCase());
  return [name, nameWithoutTag(name)].some(form => matcher.test(form));
}

function coveredByKeptPattern(model, kept) {
  return !model.restricted && (kept || []).some(entry => isAccessPattern(entry) && accessPatternMatches(entry, model.id));
}

function modelListToSave({ everything, ticked, available, kept }) {
  if (everything) return null;
  const chosen = (available || []).filter(m => ticked.has(m.id)).map(m => escapeModelName(m.id));
  return [...new Set([...chosen, ...(kept || [])])];
}

function unrestrictedIds(available) {
  return new Set((available || []).filter(m => !m.restricted).map(m => m.id));
}

function accessBoxState({ everything, ticked }, model, byPattern) {
  if (everything) return { checked: !model.restricted, disabled: false };
  return { checked: ticked.has(model.id) || byPattern, disabled: byPattern };
}

function accessModelTags(model, byPattern) {
  return [model.restricted && 'restricted', isOfflineModel(model) && OFFLINE_WORD,
    byPattern && 'allowed by a kept pattern'].filter(Boolean);
}

function toggleModelTick(state, available, modelId, on) {
  if (!state.everything) {
    const ticked = new Set(state.ticked);
    if (on) ticked.add(modelId); else ticked.delete(modelId);
    return { everything: false, ticked };
  }
  const covered = unrestrictedIds(available);
  if (on && covered.has(modelId)) return state;
  const ticked = covered;
  if (on) ticked.add(modelId); else ticked.delete(modelId);
  return { everything: false, ticked };
}

function switchEverything(state, available, on, kept) {
  const restricted = (available || []).filter(m => m.restricted).map(m => m.id);
  const dropped = on && !state.everything ? [...restricted.filter(id => state.ticked.has(id)), ...(kept || [])] : [];
  return { everything: on, ticked: unrestrictedIds(available), dropped };
}

function droppedRestrictedNote(dropped) {
  if (!dropped.length) return '';
  return `All models leaves these out, so they are no longer allowed once you save: ${dropped.join(', ')}. Saved patterns come back if you switch All models off; restricted models need ticking again.`;
}

function keptControlsShown(state) {
  return !state.everything;
}

function selectShown(state, available, shown, on) {
  const wanted = shown.filter(m => !on || !m.restricted).map(m => m.id);
  if (state.everything && on) return state;
  const ticked = state.everything ? unrestrictedIds(available) : new Set(state.ticked);
  for (const id of wanted) if (on) ticked.add(id); else ticked.delete(id);
  return { everything: false, ticked };
}

function tooManyModelsNote(list, maxModels) {
  if (!list || !maxModels || list.length <= maxModels) return '';
  return `That is ${list.length} models; an account can have at most ${maxModels}. Use "All models", or a pattern such as llama*.`;
}

function highlightIndexAfterRender(keys, previousKey) {
  const kept = previousKey ? keys.indexOf(previousKey) : -1;
  return kept >= 0 ? kept : 0;
}

function shouldRefreshModelHealth({ hidden, busy, haveModels }) {
  return !hidden && !busy && haveModels;
}

function modelAccessCount(everything, ticked, patternCovered) {
  return everything ? 'Every model except the restricted ones' : `${ticked.size + patternCovered} chosen`;
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

const ARTY_MODEL_ID = 'Arty';

function isArtyEntry(model) {
  return !!model && model.id === ARTY_MODEL_ID && model.own_tools === true;
}

function defaultChoice(listed) {
  const arty = listed.find(isArtyEntry);
  return arty ? { endpoint_id: arty.endpoint_id, model: arty.id } : firstUsableChoice(listed);
}

const REMOTE_MODEL_PREFIX = 'remote/';
const EMBEDDING_NAME = /embed/i;

function runsOnALocalModel(model) {
  return model.remote !== true && !String(model.id).startsWith(REMOTE_MODEL_PREFIX);
}

function firstUsableChoice(listed) {
  const entries = [PICKER_MODE_ARTY, PICKER_MODE_CHOSEN]
    .flatMap(mode => modelMenuEntries(listed, '', mode))
    .filter(m => !m.heading && !isAutoChoice(m) && !EMBEDDING_NAME.test(m.id));
  const first = entries.find(runsOnALocalModel) || entries[0];
  return first ? { endpoint_id: first.endpoint_id, model: first.id } : AUTO_CHOICE;
}

function modelChoiceToShow(chat, accountChoice, listed) {
  const autoOffered = offersArty(listed) || !(listed || []).length;
  for (const choice of [chat, accountChoice]) {
    if (isAutoChoice(choice) && autoOffered) return AUTO_CHOICE;
    if (isListedChoice(choice, listed)) return { endpoint_id: choice.endpoint_id, model: choice.model ?? choice.id };
  }
  return defaultChoice(listed);
}

const DOWNLOAD_PREFIX = 'hf.co/';
const DOWNLOAD_FORMAT_SUFFIX = /-gguf$/i;

function shortModelName(id) {
  const name = String(id || '');
  const served = name.indexOf('/' + DOWNLOAD_PREFIX);
  if (served > 0) return name.slice(0, served + 1) + shortModelName(name.slice(served + 1));
  if (!name.startsWith(DOWNLOAD_PREFIX)) return name;
  const file = name.slice(name.lastIndexOf('/') + 1);
  const tag = file.indexOf(':');
  return (tag >= 0 ? file.slice(0, tag) : file).replace(DOWNLOAD_FORMAT_SUFFIX, '');
}

function modelDisplayName(model) {
  if (!model) return '';
  if (isAutoChoice(model)) return AUTO_MODEL_NAME;
  return model.label || shortModelName(model.id);
}

function autoRoleChoices(listed, allowed = null) {
  const choices = new Map();
  for (const model of listed || []) {
    const offered = !Array.isArray(allowed) || (allowed.includes(model.id) && model.remote !== true && model.own_tools !== true);
    if (choices.has(model.id) || !offered) continue;
    const throughArty = model.own_tools === true && !isArtyEntry(model);
    const where = model.remote === true ? ' (remote)' : throughArty ? ' (through Arty)' : '';
    choices.set(model.id, { id: model.id, text: modelDisplayName(model) + where });
  }
  return [...choices.values()];
}

const CONTEXT_PARAM = 'num_ctx';

function fixedContextOf(model) {
  const size = model && model.fixed_context;
  return Number.isInteger(size) && size > 0 ? size : 0;
}

function fixedContextNote(size) {
  return `Fixed at ${size.toLocaleString('en-GB')} for this model, so it stays loaded instead of restarting for each chat.`;
}

function paramFieldState(key, chosen, model) {
  const fixed = key === CONTEXT_PARAM ? fixedContextOf(model) : 0;
  if (fixed) return { value: String(fixed), disabled: true, note: fixedContextNote(fixed) };
  return { value: chosen[key] ?? '', disabled: false, note: '' };
}

function chatOptionsFor(chosen, keys, model) {
  const options = {};
  for (const key of keys) {
    if (key === CONTEXT_PARAM && fixedContextOf(model)) continue;
    if (typeof chosen[key] === 'number') options[key] = chosen[key];
  }
  return Object.keys(options).length ? options : undefined;
}

function autoPickLabel(modelName, role) {
  return role ? `${modelName} \u00b7 ${AUTO_MODEL_NAME}: ${role}` : modelName;
}

function replyModelLabel(message) {
  return autoPickLabel(message.model || '', message.auto);
}

const PICKER_MODE_ARTY = 'arty';
const PICKER_MODE_CHOSEN = 'chosen';
const PICKER_TABS = Object.freeze([
  Object.freeze({ mode: PICKER_MODE_ARTY, name: 'Arty' }),
  Object.freeze({ mode: PICKER_MODE_CHOSEN, name: 'Choose a model' }),
]);

function autoModelEntry() {
  return { id: AUTO_MODEL_ID, endpoint_id: AUTO_MODEL_ID, provider: AUTO_MODEL_ID };
}

function pickerMode(current, listed = null) {
  if (Array.isArray(listed) && !offersArty(listed)) return PICKER_MODE_CHOSEN;
  return !current || isAutoChoice(current) || isArtyEntry(current) ? PICKER_MODE_ARTY : PICKER_MODE_CHOSEN;
}

function offersArty(listed) {
  return (listed || []).some(m => m.own_tools === true || (runsOnALocalModel(m) && !EMBEDDING_NAME.test(m.id)));
}

function autoMatchesSearch(query) {
  return !query || AUTO_MODEL_NAME.toLowerCase().includes(query) || AUTO_MODEL_HINT.includes(query);
}

function modelMatchesSearch(model, query) {
  return !query || model.id.toLowerCase().includes(query) || modelDisplayName(model).toLowerCase().includes(query)
    || (model.provider || '').toLowerCase().includes(query);
}

function labelledFirst(models) {
  return [...models.filter(m => m.label), ...models.filter(m => !m.label)];
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

function modelMenuEntries(listed, query, mode = PICKER_MODE_ARTY) {
  const search = (query || '').trim().toLowerCase();
  const { local, remote } = splitByRemote(distinctModels(listed).filter(m => modelMatchesSearch(m, search)));
  const remoteShown = remote.length ? [{ heading: REMOTE_SECTION }, ...remote] : [];
  if (mode === PICKER_MODE_CHOSEN || !offersArty(listed)) return [...labelledFirst(local.filter(m => !isArtyEntry(m))), ...remoteShown];
  const autoShown = autoMatchesSearch(search) ? [autoModelEntry()] : [];
  return [...local.filter(isArtyEntry), ...autoShown, ...remoteShown];
}

function nameForModelId(id, listed) {
  return modelDisplayName((listed || []).find(m => m.id === id) || { id });
}

function artyMemoryRows(entities, query) {
  const search = String(query || '').trim().toLowerCase();
  const rows = [];
  for (const entity of Array.isArray(entities) ? entities : []) {
    if (!entity || typeof entity.name !== 'string') continue;
    const type = typeof entity.type === 'string' ? entity.type : '';
    const facts = Array.isArray(entity.facts) ? entity.facts.filter(fact => typeof fact === 'string') : [];
    const entityMatches = !search || entity.name.toLowerCase().includes(search) || type.toLowerCase().includes(search);
    const shownFacts = entityMatches ? facts : facts.filter(fact => fact.toLowerCase().includes(search));
    if (entityMatches || shownFacts.length) rows.push({ name: entity.name, type, facts: shownFacts });
  }
  return rows;
}

const CONNECTION_FAILURE_WORDS = {
  timeout: 'timed out', unreachable: 'unreachable', unauthorized: 'key refused', server_error: 'server error',
  http_error: 'error answer', bad_answer: 'unreadable answer',
};
const SECONDS_PER_MINUTE = 60;

function checkedAgo(lastChecked, nowSeconds) {
  const seconds = Math.max(0, Math.round(nowSeconds - lastChecked));
  if (seconds < SECONDS_PER_MINUTE) return `checked ${seconds}s ago`;
  return `checked ${Math.floor(seconds / SECONDS_PER_MINUTE)} min ago`;
}

function connectionHealthBadge(health, nowSeconds) {
  if (!health) return { text: 'not checked yet', cls: 'mcp-state-signed-out' };
  const ago = checkedAgo(health.last_checked, nowSeconds);
  if (health.available) return { text: `Available · ${ago}`, cls: 'mcp-state-connected' };
  const why = CONNECTION_FAILURE_WORDS[health.error_kind] || 'not answering';
  return { text: `Unavailable (${why}) · ${ago}`, cls: 'mcp-state-failed' };
}

const OFFLINE_WORD = 'offline';

function isOfflineModel(model) {
  return !!model && model.offline === true;
}

function withoutOffline(list) {
  return (list || []).filter(m => !isOfflineModel(m));
}

function offlineTooltip(model, nowSeconds) {
  const health = model.health || {};
  const why = CONNECTION_FAILURE_WORDS[health.error_kind] || 'not answering';
  const ago = health.last_checked ? checkedAgo(health.last_checked, nowSeconds) : 'not checked yet';
  return `Offline: ${model.connection || 'this connection'} is ${why}, ${ago}`;
}

function offlineNotice(model) {
  return `${model.id} is offline right now (${model.connection || 'its connection'}). Pick another model, or try again when it is back.`;
}

const CUT_OFF_NOTE = '\n\n(This reply hit the length limit and was cut off here. Use Continue to carry on.)';

const THINKING_ONLY_NOTE = 'The model used its whole length limit on thinking and wrote no answer. Try again or ask for a shorter answer.';

function withoutCutOffNote(text) {
  if (typeof text !== 'string') return text;
  const kept = text.trimEnd();
  if ([THINKING_ONLY_NOTE, CUT_OFF_NOTE.trim()].includes(kept.trim())) return '';
  return kept.endsWith(CUT_OFF_NOTE) ? kept.slice(0, -CUT_OFF_NOTE.length) : text;
}

function isCutOffNote(delta) {
  return delta === CUT_OFF_NOTE || delta === THINKING_ONLY_NOTE;
}

function sameModelSet(before, after) {
  const keys = list => new Set((list || []).map(m => `${m.endpoint_id}\u0000${m.id}`));
  const left = keys(before);
  const right = keys(after);
  return left.size === right.size && [...left].every(key => right.has(key));
}

function persistedChoice(choice) {
  if (!choice || choice.persist !== true || !choice.model) return null;
  return { endpoint_id: choice.endpoint_id, model: choice.model };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    persistedChoice,
    isLocalMediaUrl, isCodeLanguageClass, toolOutcome, toolOutcomeLine, CUT_OFF_NOTE, THINKING_ONLY_NOTE, withoutCutOffNote, isCutOffNote,
    modelAccessSummary, OFFLINE_WORD, isOfflineModel, withoutOffline, offlineTooltip, offlineNotice, sameModelSet,
    groupModelsByConnection, REMOTE_SECTION, splitByRemote, accessSections, accessMatchesSearch, ticksFromAccess, modelListToSave, modelAccessCount,
    accessBoxState, accessModelTags, toggleModelTick, switchEverything, droppedRestrictedNote, keptControlsShown, selectShown,
    escapeModelName, isAccessPattern, accessPatternMatches, coveredByKeptPattern, tooManyModelsNote,
    highlightIndexAfterRender, shouldRefreshModelHealth,
    DAY_MS, TOAST_MS, TOAST_ERROR_MS, TOAST_FADE_MS,
    sentenceCase, dateGroupLabel, messageStamp, themeChoiceFrom, themeIsLight, showToast, removalQuestion, confirmRemove,
    pendingApprovalCards, UNTRUSTED_APPROVAL_NOTE, UNSANDBOXED_SHELL_NOTE, shellApprovalNotes, approvalHeading, resolveChatControls, isLastAssistantMessage,
    CHAT_TITLE_CHARS, submitOnEnter, closeOnEscape, chatTitleFrom, firstUnusedModel,
    MAX_SPOKEN_CHARS, takeSpokenSentences, speakableText, spokenPieces,
    REGENERATE_WITH, regenerateChoice,
    menuFocusIndex, menuKeyHandler,
    showListLoading,
    failureMessage,
    NO_EMAIL_ACCOUNTS, ruleAccountChoices,
    AUTO_MODEL_ID, AUTO_MODEL_NAME, AUTO_MODEL_HINT, AUTO_CHOICE, isAutoChoice, modelChoiceToShow,
    fixedContextNote, paramFieldState, chatOptionsFor, autoRoleChoices, autoPickLabel, replyModelLabel,
    autoModelEntry, PICKER_MODE_ARTY, PICKER_MODE_CHOSEN, PICKER_TABS, pickerMode, offersArty, distinctModels, modelMenuEntries,
    ARTY_MODEL_ID, isArtyEntry, shortModelName, modelDisplayName, nameForModelId,
    BACKEND_RUNS_ON_ITS_OWN, NO_LOCAL_OLLAMA, FOUND_NOT_ADDED, backendStatusPlan, scanEndpointBody, scanSummary, addedLocalMessage,
    artyMemoryRows, connectionHealthBadge,
  };
}
