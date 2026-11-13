'use strict';

const TOOL_TOGGLES = [
  ['toggleSearchWeb', 'web search'],
  ['toggleDeepResearch', 'deep research'],
  ['toggleSearchDocuments', 'search knowledge'],
  ['toggleManageMemory', 'memory'],
  ['toggleManageNotes', 'notes & to-dos'],
  ['toggleManageTasks', 'scheduled tasks'],
  ['toggleManageEmail', 'email'],
  ['toggleGenerateImage', 'image generation'],
  ['toggleFindImage', 'find images'],
  ['toggleGenerateAudio', 'speech generation'],
  ['toggleGenerateVideo', 'video generation'],
  ['toggleGenerateDocument', 'document generation'],
  ['toggleGithubCli', 'github cli'],
  ['toggleEditFile', 'edit files (writes to disk)'],
  ['toggleRunShell', 'run shell (no sandbox)'],
];

function renderPlusMenuTools() {
  const box = $('plusMenuTools');
  box.innerHTML = '';
  for (const [id, label] of TOOL_TOGGLES) {
    const source = $(id);
    if (!source) continue;
    const row = document.createElement('label');
    row.innerHTML = `<input type="checkbox" ${source.checked ? 'checked' : ''} /> <span>${escapeHtml(sentenceCase(label))}</span>`;
    row.querySelector('input').addEventListener('change', () => { source.click(); });
    box.appendChild(row);
  }
}

function refreshActiveTools() {
  const box = $('activeTools');
  box.innerHTML = '';
  for (const [id, label] of TOOL_TOGGLES) {
    const source = $(id);
    if (!source || !source.checked) continue;
    const chip = document.createElement('span');
    chip.className = 'chip tool';
    chip.innerHTML = `<span class="chip-label">${escapeHtml(sentenceCase(label))}</span><button type="button" aria-label="Turn off ${escapeHtml(label)}">×</button>`;
    chip.querySelector('button').onclick = () => source.click();
    box.appendChild(chip);
  }
  if (enabledMcpServers.size) {
    const chip = document.createElement('span');
    chip.className = 'chip tool';
    chip.innerHTML = `<span class="chip-label">${enabledMcpServers.size} MCP server(s)</span>`;
    box.appendChild(chip);
  }
}

TOOL_TOGGLES.forEach(([id]) => { const el = $(id); if (el) el.addEventListener('change', refreshActiveTools); });

function plusMenuItems() {
  return [...$('plusMenu').querySelectorAll('[role="menuitem"], #plusMenuTools input')]
    .filter(item => item.getClientRects().length);
}

function closePlusMenu() {
  $('plusMenu').hidden = true;
  $('plusBtn').setAttribute('aria-expanded', 'false');
  $('plusBtn').focus();
}

$('plusBtn').onclick = e => {
  e.stopPropagation();
  const menu = $('plusMenu');
  const opening = menu.hidden;
  closeAllMenus();
  if (opening) renderPlusMenuTools();
  menu.hidden = !opening;
  $('plusBtn').setAttribute('aria-expanded', String(opening));
  if (opening) { const first = plusMenuItems()[0]; if (first) first.focus(); }
};

$('plusMenu').addEventListener('keydown', menuKeyHandler(plusMenuItems, closePlusMenu));

$('plusMenu').addEventListener('click', e => {
  const act = e.target.closest('[data-act]');
  if (!act) return;
  $('plusMenu').hidden = true;
  $('plusBtn').setAttribute('aria-expanded', 'false');
  if (act.dataset.act === 'upload') $('attachFileInput').click();
  else if (act.dataset.act === 'knowledge') insertTrigger('#');
  else if (act.dataset.act === 'prompts') insertTrigger('/');
  else if (act.dataset.act === 'tool-settings') openSettings('tools');
});

function insertTrigger(char) {
  const input = els.input;
  if (char === '/') input.value = '/';
  else input.value += (input.value && !/\s$/.test(input.value) ? ' ' : '') + char;
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  updateComposerPopup();
}
