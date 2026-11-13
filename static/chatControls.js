'use strict';

const CHAT_PARAMS = [
  { key: 'temperature', label: 'temperature', step: '0.05', min: '0', max: '2' },
  { key: 'top_p', label: 'top p', step: '0.05', min: '0', max: '1' },
  { key: 'top_k', label: 'top k', step: '1', min: '0' },
  { key: 'num_predict', label: 'max tokens', step: '1', min: '1' },
  { key: 'num_ctx', label: 'context length', step: '256', min: '256' },
  { key: 'repeat_penalty', label: 'repeat penalty', step: '0.05', min: '0' },
  { key: 'seed', label: 'seed', step: '1', min: '0' },
];

let chatParams = {};
try { chatParams = JSON.parse(localStorage.getItem('sf_params') || '{}') || {}; } catch (_) {}

function defaultChatControls() {
  let systemPrompt = '';
  let params = {};
  try {
    systemPrompt = localStorage.getItem('sf_sys') || '';
    params = JSON.parse(localStorage.getItem('sf_params') || '{}') || {};
  } catch (_) {}
  return { system_prompt: systemPrompt, params };
}

function saveDefaultChatControls(controls) {
  try {
    localStorage.setItem('sf_sys', controls.system_prompt);
    localStorage.setItem('sf_params', JSON.stringify(controls.params));
  } catch (_) {}
}

function currentChatControls() {
  return { system_prompt: els.sysPrompt.value, params: { ...chatParams } };
}

function chatHasOwnControls() {
  return !!currentSessionId && !isTemporaryChat();
}

function updateControlsScope() {
  const own = chatHasOwnControls();
  $('controlsScope').textContent = own
    ? 'Saved with this chat only. New chats start from your defaults.'
    : 'These are your defaults. Each chat keeps its own copy once it is saved.';
  $('controlsUseDefaultsBtn').hidden = !own;
  $('controlsSaveDefaultBtn').hidden = !own;
}

function showChatControls(controls) {
  els.sysPrompt.value = controls.system_prompt;
  chatParams = { ...controls.params };
  renderParamGrid();
  updateControlsScope();
}

function showSessionControls(session) {
  showChatControls(resolveChatControls(session, defaultChatControls()));
}

let controlsSaveTimer = null;

function onChatControlsEdited() {
  if (!chatHasOwnControls()) {
    saveDefaultChatControls(currentChatControls());
    return;
  }
  const sessionId = currentSessionId;
  const controls = currentChatControls();
  clearTimeout(controlsSaveTimer);
  controlsSaveTimer = setTimeout(() => {
    apiJson('/api/chat/sessions/' + sessionId, { method: 'PUT', body: controls })
      .catch(e => showToast(`Could not save the chat controls: ${e.message}`, 'error'));
  }, CONTROLS_SAVE_DEBOUNCE_MS);
}

function renderParamGrid() {
  const grid = $('paramGrid');
  grid.innerHTML = '';
  for (const param of CHAT_PARAMS) {
    const id = 'param_' + param.key;
    const label = document.createElement('label');
    label.htmlFor = id;
    label.textContent = param.label;
    const input = document.createElement('input');
    Object.assign(input, { id, type: 'number', step: param.step, placeholder: 'default' });
    if (param.min) input.min = param.min;
    if (param.max) input.max = param.max;
    input.value = chatParams[param.key] ?? '';
    input.addEventListener('input', () => {
      const value = input.value === '' ? null : Number(input.value);
      if (value === null || Number.isNaN(value)) delete chatParams[param.key];
      else chatParams[param.key] = value;
      onChatControlsEdited();
    });
    grid.append(label, input);
  }
}

function chatOptions() {
  const options = {};
  for (const { key } of CHAT_PARAMS) {
    if (typeof chatParams[key] === 'number') options[key] = chatParams[key];
  }
  return Object.keys(options).length ? options : undefined;
}

function toggleControls(force) {
  const panel = $('controlsPanel');
  panel.hidden = typeof force === 'boolean' ? !force : !panel.hidden;
  $('controlsBtn').classList.toggle('active', !panel.hidden);
  $('controlsScrim').hidden = panel.hidden || !isNarrowScreen();
}

$('controlsBtn').onclick = () => toggleControls();
$('controlsScrim').onclick = () => toggleControls(false);
$('controlsCloseBtn').onclick = () => toggleControls(false);
$('resetParamsBtn').onclick = () => { chatParams = {}; renderParamGrid(); onChatControlsEdited(); };
$('controlsUseDefaultsBtn').onclick = () => { showChatControls(defaultChatControls()); onChatControlsEdited(); };
$('controlsSaveDefaultBtn').onclick = () => {
  saveDefaultChatControls(currentChatControls());
  showToast('Saved as your defaults for new chats');
};
renderParamGrid();
updateControlsScope();

$('exportBtn').onclick = e => {
  e.stopPropagation();
  const menu = $('exportMenu');
  const opening = menu.hidden;
  closeAllMenus();
  menu.hidden = !opening;
};
$('exportMenu').querySelectorAll('[data-format]').forEach(btn => {
  btn.onclick = () => { $('exportMenu').hidden = true; exportChat(btn.dataset.format); };
});
