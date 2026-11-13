'use strict';

let presetCache = [];

function modelKey(m) { return m.endpoint_id + '\u0000' + m.id; }

function selectModel(endpointId, modelId) {
  const opt = [...els.modelList.options].find(o => o.value === modelId && o.dataset.endpointId === endpointId);
  if (!opt || opt.selected) return false;
  els.modelList.value = opt.value;
  opt.selected = true;
  els.modelList.dispatchEvent(new Event('change'));
  return true;
}

function storedDefaultModel() {
  try { return JSON.parse(localStorage.getItem('sf_default_model') || 'null'); } catch (_) { return null; }
}

function selectDefaultModel() {
  const preferred = storedDefaultModel();
  if (!preferred) return;
  const opt = [...els.modelList.options].find(o => o.value === preferred.id && o.dataset.endpointId === preferred.endpoint_id);
  if (opt) opt.selected = true;
}

function renderModelPicker() {
  const model = currentModel();
  $('modelPickerLabel').textContent = model ? model.id : (models.length ? 'select a model' : 'no models');
  const preferred = storedDefaultModel();
  const isDefault = model && preferred && preferred.id === model.id && preferred.endpoint_id === model.endpoint_id;
  const star = $('setDefaultModelBtn');
  star.innerHTML = icon('star', isDefault ? 'solid' : '');
  star.classList.toggle('active', !!isDefault);
  star.setAttribute('aria-pressed', String(!!isDefault));
  star.setAttribute('aria-label', isDefault ? 'This is your default model' : 'Set as default model');
  star.title = isDefault ? 'This is your default model' : 'Set as default model';
  if (!$('modelMenu').hidden) renderModelMenu();
}

function renderModelMenu() {
  const q = $('modelSearch').value.trim().toLowerCase();
  const list = $('modelMenuList');
  const current = currentModel();
  list.innerHTML = '';
  const matches = models.filter(m => !q || m.id.toLowerCase().includes(q) || (m.provider || '').toLowerCase().includes(q));
  for (const m of matches) {
    const li = document.createElement('li');
    li.className = 'model-option' + (current && modelKey(current) === modelKey(m) ? ' selected' : '');
    li.setAttribute('role', 'option');
    const provider = window.providerLabel ? window.providerLabel(m.provider) : m.provider;
    const size = m.size ? (m.size / 1073741824).toFixed(1) + ' GB · ' : '';
    li.innerHTML = `<span class="model-option-name">${escapeHtml(m.id)}</span><span class="model-option-meta">${escapeHtml(size + provider)}</span>`;
    li.onclick = () => {
      selectModel(m.endpoint_id, m.id);
      closeAllMenus();
    };
    list.appendChild(li);
  }
  const presets = presetCache.filter(p => !q || p.name.toLowerCase().includes(q));
  if (presets.length) {
    const heading = document.createElement('li');
    heading.className = 'model-menu-heading';
    heading.textContent = 'workspace models (presets)';
    list.appendChild(heading);
    for (const p of presets) {
      const li = document.createElement('li');
      li.className = 'model-option';
      li.setAttribute('role', 'option');
      li.innerHTML = `<span class="model-option-name">${escapeHtml(p.name)}</span><span class="model-option-meta">${escapeHtml(p.model || '')}</span>`;
      li.onclick = () => {
        applyPreset(p);
        els.modelList.dispatchEvent(new Event('change'));
        refreshActiveTools();
        closeAllMenus();
      };
      list.appendChild(li);
    }
  }
  if (!list.children.length) list.innerHTML = '<li class="model-menu-heading">no matches</li>';
  const first = list.querySelector('.model-option');
  if (first) first.classList.add('highlight');
}

async function openModelMenu() {
  const menu = $('modelMenu');
  closeAllMenus(menu);
  menu.hidden = false;
  $('modelPickerBtn').setAttribute('aria-expanded', 'true');
  $('modelSearch').value = '';
  renderModelMenu();
  $('modelSearch').focus();
  try {
    ({ presets: presetCache = [] } = await apiJson('/api/presets'));
    if (!menu.hidden) renderModelMenu();
  } catch (_) {}
}

$('modelPickerBtn').onclick = () => {
  if ($('modelMenu').hidden) openModelMenu();
  else closeAllMenus();
};
$('modelSearch').addEventListener('input', renderModelMenu);
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

$('setDefaultModelBtn').onclick = () => {
  const model = currentModel();
  if (!model) return;
  try { localStorage.setItem('sf_default_model', JSON.stringify({ endpoint_id: model.endpoint_id, id: model.id })); } catch (_) {}
  renderModelPicker();
  showToast(`${model.id} is now your default model`);
};
