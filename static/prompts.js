'use strict';

let promptCache = [];
let editingPromptId = null;

async function loadPrompts() {
  try {
    ({ prompts: promptCache = [] } = await apiJson('/api/prompts'));
  } catch (_) {
    promptCache = [];
  }
  return promptCache;
}

function resetPromptForm() {
  editingPromptId = null;
  $('promptCommand').value = '';
  $('promptTitle').value = '';
  $('promptContent').value = '';
  $('promptFormTitle').textContent = 'new prompt';
  $('cancelPromptEditBtn').hidden = true;
}

async function refreshPromptList() {
  const list = $('promptList');
  await loadPrompts();
  list.innerHTML = promptCache.length ? '' : '<li class="settings-hint">no prompts yet</li>';
  for (const p of promptCache) {
    const li = document.createElement('li');
    li.className = 'endpoint-row';
    li.innerHTML =
      `<span class="endpoint-label">/${escapeHtml(p.command)}</span>` +
      `<span class="endpoint-meta">${escapeHtml(p.title)}</span>` +
      '<button class="btn ghost" type="button" data-act="edit">edit</button>' +
      '<button class="endpoint-remove" type="button" title="Delete" aria-label="Delete">×</button>';
    li.querySelector('[data-act="edit"]').onclick = () => {
      editingPromptId = p.id;
      $('promptCommand').value = p.command;
      $('promptTitle').value = p.title;
      $('promptContent').value = p.content;
      $('promptFormTitle').textContent = 'edit /' + p.command;
      $('cancelPromptEditBtn').hidden = false;
    };
    li.querySelector('.endpoint-remove').onclick = async () => {
      const ok = await showDialog({ title: 'delete prompt', text: `Delete /${p.command}?`, okLabel: 'delete', danger: true });
      if (!ok) return;
      try { await apiJson('/api/prompts/' + p.id, { method: 'DELETE' }); }
      catch (e) { setHint($('promptSaveResult'), e.message, 'error'); }
      if (editingPromptId === p.id) resetPromptForm();
      refreshPromptList();
    };
    list.appendChild(li);
  }
}

$('savePromptBtn').onclick = async () => {
  const body = { command: $('promptCommand').value, title: $('promptTitle').value, content: $('promptContent').value };
  try {
    if (editingPromptId) await apiJson('/api/prompts/' + editingPromptId, { method: 'PUT', body });
    else await apiJson('/api/prompts', { method: 'POST', body });
    setHint($('promptSaveResult'), 'saved', 'ok');
    resetPromptForm();
    refreshPromptList();
  } catch (e) {
    setHint($('promptSaveResult'), e.message, 'error');
  }
};
$('cancelPromptEditBtn').onclick = resetPromptForm;

async function expandPromptVariables(content) {
  const now = new Date();
  let clipboard = '';
  if (content.includes('{{CLIPBOARD}}')) {
    try { clipboard = await navigator.clipboard.readText(); } catch (_) { clipboard = ''; }
  }
  return content
    .replaceAll('{{CURRENT_DATE}}', now.toLocaleDateString())
    .replaceAll('{{CURRENT_TIME}}', now.toLocaleTimeString())
    .replaceAll('{{CURRENT_WEEKDAY}}', now.toLocaleDateString(undefined, { weekday: 'long' }))
    .replaceAll('{{CLIPBOARD}}', clipboard);
}
