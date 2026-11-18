'use strict';

function modelKey(m) { return m.endpoint_id + '\u0000' + m.id; }

function modelOptionMeta(m) {
  if (isAutoModel(m)) return AUTO_MODEL_HINT;
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
  return isAutoModel(model) ? AUTO_MODEL_NAME : model.id;
}

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
  li.className = 'model-option' + (isAutoModel(m) ? ' auto-option' : '') + (current && modelKey(current) === modelKey(m) ? ' selected' : '');
  li.setAttribute('role', 'option');
  li.innerHTML = `<span class="model-option-name">${escapeHtml(modelPickerText(m))}</span><span class="model-option-meta">${escapeHtml(modelOptionMeta(m))}</span>`;
  li.onclick = () => {
    pickModel(m);
    closeAllMenus();
  };
  return li;
}

function renderPickerMode(current) {
  const mode = pickerMode(current);
  for (const [id, shownMode] of [['modelModeAuto', PICKER_MODE_AUTO], ['modelModeChosen', PICKER_MODE_CHOSEN]]) {
    const pressed = mode === shownMode;
    $(id).classList.toggle('active', pressed);
    $(id).setAttribute('aria-pressed', String(pressed));
  }
}

function highlightFirstChosenModel() {
  const options = [...$('modelMenuList').querySelectorAll('.model-option')];
  const firstModel = options.find(o => !o.classList.contains('auto-option')) || options[0];
  options.forEach(o => o.classList.toggle('highlight', o === firstModel));
  if (firstModel) firstModel.scrollIntoView({ block: 'nearest' });
}

function renderModelMenu() {
  const q = $('modelSearch').value.trim().toLowerCase();
  const list = $('modelMenuList');
  const current = currentModel();
  renderPickerMode(current);
  list.innerHTML = '';
  for (const m of modelMenuEntries(models, q)) list.appendChild(modelMenuOption(m, current));
  if (!list.children.length) list.innerHTML = '<li class="model-menu-heading">no matches</li>';
  const first = list.querySelector('.model-option');
  if (first) first.classList.add('highlight');
}

function openModelMenu() {
  const menu = $('modelMenu');
  closeAllMenus(menu);
  menu.hidden = false;
  $('modelPickerBtn').setAttribute('aria-expanded', 'true');
  $('modelSearch').value = '';
  renderModelMenu();
  $('modelSearch').focus();
}

$('modelPickerBtn').onclick = () => {
  if ($('modelMenu').hidden) openModelMenu();
  else closeAllMenus();
};
$('modelSearch').addEventListener('input', renderModelMenu);
$('modelModeAuto').onclick = () => {
  pickModel(autoModelEntry());
  closeAllMenus();
  $('modelPickerBtn').focus();
};
$('modelModeChosen').onclick = () => {
  highlightFirstChosenModel();
  $('modelSearch').focus();
};
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
