'use strict';

const MODEL_HEALTH_REFRESH_MS = 60000;

function modelKey(m) { return m.endpoint_id + '\u0000' + m.id; }

function modelOptionMeta(m) {
  if (isAutoModel(m)) return AUTO_MODEL_HINT;
  if (isOfflineModel(m)) return OFFLINE_WORD;
  const provider = window.providerLabel ? window.providerLabel(m.provider) : m.provider;
  const size = m.size ? (m.size / 1073741824).toFixed(1) + ' GB · ' : '';
  const server = m.server_note ? ' · ' + m.server_note : '';
  return size + provider + server;
}

function selectModel(endpointId, modelId) {
  const opt = [...els.modelList.options].find(o => o.value === modelId && o.dataset.endpointId === endpointId);
  if (!opt || opt.selected) return false;
  els.modelList.value = opt.value;
  opt.selected = true;
  els.modelList.dispatchEvent(new Event('change'));
  return true;
}

function showModelChoice(choice) {
  const shown = modelChoiceToShow(choice, accountModelChoice, models);
  if (!selectModel(shown.endpoint_id, shown.model)) renderModelPicker();
}

function choiceOf(model) {
  return { endpoint_id: model.endpoint_id, model: model.id };
}

async function rememberModelChoice(model) {
  const choice = choiceOf(model);
  accountModelChoice = choice;
  renderModelPicker();
  try {
    await apiJson('/api/model-choice', { method: 'PUT', body: choice });
  } catch (e) {
    showToast('Could not save your model choice: ' + e.message, 'error');
  }
}

async function keepModelForOpenChat(model) {
  if (!currentSessionId || isTemporaryChat()) return;
  try {
    await apiJson('/api/chat/sessions/' + currentSessionId, { method: 'PUT', body: choiceOf(model) });
  } catch (e) {
    showToast('Could not save this chat\'s model: ' + e.message, 'error');
  }
}

function pickModel(model) {
  selectModel(model.endpoint_id, model.id);
  rememberModelChoice(model);
  keepModelForOpenChat(model);
}

function modelPickerText(model) {
  if (!model) return models.length ? 'select a model' : 'no models';
  if (isAutoModel(model)) return AUTO_MODEL_NAME;
  const name = modelDisplayName(model);
  return isOfflineModel(model) ? `${name} (${OFFLINE_WORD})` : name;
}

let pickerTab = null;

function shownPickerTab(current) {
  return pickerTab || pickerMode(current, models);
}

function onlineModels() {
  return withoutOffline(models);
}

let noticeShownFor = '';

function showModelStatus() {
  if (busy) return;
  const model = currentModel();
  const down = isOfflineModel(model);
  setStatus(down ? 'error' : 'ready', modelPickerText(model));
  if (!down) { noticeShownFor = ''; return; }
  if (noticeShownFor === modelKey(model)) return;
  noticeShownFor = modelKey(model);
  showToast(offlineNotice(model), 'error');
}

function mayRefreshModelHealth() {
  return shouldRefreshModelHealth({ hidden: document.hidden, busy, haveModels: models.length > 0 });
}

let modelListsAsked = 0;

function askForModelList() {
  modelListsAsked += 1;
  return modelListsAsked;
}

function isNewestModelList(asked) {
  return asked === modelListsAsked;
}

async function refreshModelHealth() {
  if (!mayRefreshModelHealth()) return;
  const asked = askForModelList();
  let answer;
  try {
    answer = await apiJson('/api/models');
  } catch (_) {
    return;
  }
  if (!isNewestModelList(asked) || !mayRefreshModelHealth()) return;
  if (!sameModelSet(models, answer.models)) { await loadModels({ quiet: true }); return; }
  models = answer.models;
  autoEverydayModel = answer.auto_everyday;
  renderModelPicker();
  showModelStatus();
}

async function refuseOfflineModel(model) {
  if (!isOfflineModel(model)) return false;
  await refreshModelHealth();
  const fresh = isAutoModel(model) ? model : models.find(m => modelKey(m) === modelKey(model));
  if (!isOfflineModel(fresh)) return false;
  showToast(offlineNotice(fresh), 'error');
  return true;
}

setInterval(refreshModelHealth, MODEL_HEALTH_REFRESH_MS);

function renderModelPicker() {
  const model = currentModel();
  $('modelPickerLabel').textContent = modelPickerText(model);
  const preferred = accountModelChoice;
  const isDefault = model && preferred && preferred.model === model.id && preferred.endpoint_id === model.endpoint_id;
  const star = $('setDefaultModelBtn');
  star.innerHTML = icon('star', isDefault ? 'solid' : '');
  star.classList.toggle('active', !!isDefault);
  star.setAttribute('aria-pressed', String(!!isDefault));
  star.setAttribute('aria-label', isDefault ? 'This is your default model' : 'Set as default model');
  star.title = isDefault ? 'This is your default model' : 'Set as default model';
  if (!$('modelMenu').hidden) renderModelMenu();
}

function modelMenuOption(m, current) {
  const li = document.createElement('li');
  const down = isOfflineModel(m);
  li.className = 'model-option' + (isAutoModel(m) ? ' auto-option' : '') + (down ? ' offline' : '') + (current && modelKey(current) === modelKey(m) ? ' selected' : '');
  li.setAttribute('role', 'option');
  li.dataset.key = modelKey(m);
  const shownName = down ? modelDisplayName(m) : modelPickerText(m);
  li.innerHTML = `<span class="model-option-name">${escapeHtml(shownName)}</span><span class="model-option-meta">${escapeHtml(modelOptionMeta(m))}</span>`;
  if (down) {
    li.setAttribute('aria-disabled', 'true');
    li.title = offlineTooltip(m, Date.now() / 1000);
    return li;
  }
  li.onclick = () => {
    pickModel(m);
    closeAllMenus();
  };
  return li;
}

function modelMenuHeading(text) {
  const li = document.createElement('li');
  li.className = 'model-menu-heading';
  li.setAttribute('role', 'presentation');
  li.textContent = text;
  return li;
}

function renderPickerMode(current) {
  const mode = shownPickerTab(current);
  $('modelModeArty').hidden = !offersArty(models);
  for (const [id, shownMode] of [['modelModeArty', PICKER_MODE_ARTY], ['modelModeChosen', PICKER_MODE_CHOSEN]]) {
    const pressed = mode === shownMode;
    $(id).classList.toggle('active', pressed);
    $(id).setAttribute('aria-pressed', String(pressed));
  }
}

function highlightFirstModel() {
  const options = [...$('modelMenuList').querySelectorAll('.model-option')];
  const firstModel = options[0];
  options.forEach(o => o.classList.toggle('highlight', o === firstModel));
  if (firstModel) firstModel.scrollIntoView({ block: 'nearest' });
}

function showPickerTab(mode) {
  pickerTab = mode;
  renderModelMenu();
  highlightFirstModel();
  $('modelSearch').focus();
}

function renderModelMenu() {
  const q = $('modelSearch').value.trim().toLowerCase();
  const list = $('modelMenuList');
  const current = currentModel();
  const highlighted = list.querySelector('.model-option.highlight');
  const highlightedKey = highlighted ? highlighted.dataset.key : '';
  renderPickerMode(current);
  list.innerHTML = '';
  for (const m of modelMenuEntries(models, q, shownPickerTab(current))) list.appendChild(m.heading ? modelMenuHeading(m.heading) : modelMenuOption(m, current));
  if (!list.children.length) list.innerHTML = '<li class="model-menu-heading">no matches</li>';
  const options = [...list.querySelectorAll('.model-option')];
  const keep = options[highlightIndexAfterRender(options.map(o => o.dataset.key), highlightedKey)];
  if (keep) keep.classList.add('highlight');
}

function openModelMenu() {
  const menu = $('modelMenu');
  closeAllMenus(menu);
  menu.hidden = false;
  $('modelPickerBtn').setAttribute('aria-expanded', 'true');
  $('modelSearch').value = '';
  pickerTab = pickerMode(currentModel(), models);
  renderModelMenu();
  refreshModelHealth();
  $('modelSearch').focus();
}

$('modelPickerBtn').onclick = () => {
  if ($('modelMenu').hidden) openModelMenu();
  else closeAllMenus();
};
$('modelSearch').addEventListener('input', renderModelMenu);
$('modelModeArty').onclick = () => showPickerTab(PICKER_MODE_ARTY);
$('modelModeChosen').onclick = () => showPickerTab(PICKER_MODE_CHOSEN);
$('modelSearch').addEventListener('keydown', e => {
  const options = [...$('modelMenuList').querySelectorAll('.model-option')];
  const at = options.findIndex(o => o.classList.contains('highlight'));
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    if (!options.length) return;
    const next = Math.max(0, Math.min(options.length - 1, at + (e.key === 'ArrowDown' ? 1 : -1)));
    options.forEach((o, i) => o.classList.toggle('highlight', i === next));
    options[next].scrollIntoView({ block: 'nearest' });
  } else if (e.key === 'Enter') {
    e.preventDefault();
    if (options[at]) options[at].click();
  }
});
$('modelSearch').addEventListener('keydown', closeOnEscape(() => {
  closeAllMenus();
  $('modelPickerBtn').focus();
}));

$('setDefaultModelBtn').onclick = async () => {
  const model = currentModel();
  if (!model) return;
  await rememberModelChoice(model);
  showToast(`${modelPickerText(model)} is now your default model`);
};
