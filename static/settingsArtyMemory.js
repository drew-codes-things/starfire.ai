'use strict';

const ARTY_FACT_MAX_CHARS = 300;
let artyMemoryEntities = [];

function artyMemoryElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function artyMemoryButton(className, text, label) {
  const button = artyMemoryElement('button', className, text);
  button.type = 'button';
  if (label) {
    button.title = label;
    button.setAttribute('aria-label', label);
  }
  return button;
}

function showArtyMemoryNote(target, text, isError) {
  target.textContent = text;
  target.className = 'settings-hint' + (isError ? ' error' : '');
}

async function askArtyMemory(action, body) {
  const r = await fetch('/api/arty-memory/' + action, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await failureMessage(r, `Arty could not ${action} that (error ${r.status})`));
  return r.json();
}

async function changeArtyMemory(action, body, noteTarget, doneText) {
  try {
    await askArtyMemory(action, body);
  } catch (e) {
    showArtyMemoryNote(noteTarget, e.message, true);
    return false;
  }
  await refreshArtyMemory();
  showArtyMemoryNote(noteTarget, doneText, false);
  return true;
}

async function refreshArtyMemory() {
  try {
    const r = await fetch('/api/arty-memory');
    if (!r.ok) throw new Error(await failureMessage(r, `could not load Arty's memory (error ${r.status})`));
    const { entities = [] } = await r.json();
    artyMemoryEntities = entities;
  } catch (e) {
    artyMemoryEntities = [];
    settingsEls.artyMemoryList.replaceChildren();
    showArtyMemoryNote(settingsEls.artyMemoryStatus, e.message, true);
    return;
  }
  showArtyMemoryNote(settingsEls.artyMemoryStatus, '', false);
  renderArtyMemory();
}

function artyEntityRow(row) {
  const li = artyMemoryElement('li', 'endpoint-row arty-memory-entity');
  const remove = artyMemoryButton('endpoint-remove', '×', `Remove ${row.name} and everything saved about it`);
  remove.onclick = async () => {
    if (!await confirmRemove(`${row.name} and everything Arty saved about it`)) return;
    await changeArtyMemory('delete', { entity: row.name }, settingsEls.artyMemoryStatus, `Removed ${row.name}.`);
  };
  li.append(artyMemoryElement('strong', 'endpoint-label', row.name), artyMemoryElement('span', 'endpoint-meta', row.type),
    remove);
  return li;
}

function startArtyFactEdit(li, entity, fact) {
  const input = artyMemoryElement('input');
  input.type = 'text';
  input.value = fact;
  input.maxLength = ARTY_FACT_MAX_CHARS;
  input.setAttribute('aria-label', `New wording for this fact about ${entity}`);
  const save = artyMemoryButton('btn send', 'Save');
  const cancel = artyMemoryButton('btn ghost', 'Cancel');
  save.onclick = async () => {
    const wording = input.value.trim();
    if (!wording) return;
    save.disabled = true;
    const saved = await changeArtyMemory('edit', { entity, old: fact, new: wording }, settingsEls.artyMemoryStatus,
      'Saved.');
    if (!saved) save.disabled = false;
  };
  cancel.onclick = renderArtyMemory;
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') save.onclick();
    if (e.key === 'Escape') renderArtyMemory();
  });
  li.replaceChildren(input, save, cancel);
  input.focus();
}

function artyFactRow(entity, fact) {
  const li = artyMemoryElement('li', 'endpoint-row arty-memory-fact');
  const edit = artyMemoryButton('btn ghost', 'Edit', `Edit this fact about ${entity}`);
  const remove = artyMemoryButton('endpoint-remove', '×', `Remove this fact about ${entity}`);
  edit.onclick = () => startArtyFactEdit(li, entity, fact);
  remove.onclick = async () => {
    if (!await confirmRemove(`this fact about ${entity}`)) return;
    await changeArtyMemory('delete', { entity, fact }, settingsEls.artyMemoryStatus, 'Removed.');
  };
  li.append(artyMemoryElement('span', 'endpoint-label', fact), edit, remove);
  return li;
}

function renderArtyMemory() {
  const rows = artyMemoryRows(artyMemoryEntities, settingsEls.artyMemorySearch.value);
  const list = settingsEls.artyMemoryList;
  list.replaceChildren();
  if (!rows.length) {
    const empty = artyMemoryEntities.length ? 'nothing matches that search' : 'Arty has nothing saved yet';
    list.append(artyMemoryElement('li', 'settings-hint', empty));
    return;
  }
  for (const row of rows) list.append(artyEntityRow(row), ...row.facts.map(fact => artyFactRow(row.name, fact)));
}

settingsEls.artyMemorySearch.addEventListener('input', renderArtyMemory);
settingsEls.refreshArtyMemoryBtn.onclick = refreshArtyMemory;
settingsEls.addArtyMemoryBtn.onclick = async () => {
  const entity = settingsEls.artyMemoryEntity.value.trim();
  const fact = settingsEls.artyMemoryFact.value.trim();
  if (!entity || !fact) {
    showArtyMemoryNote(settingsEls.artyMemoryAddResult, 'Fill in who or what it is about and the fact.', true);
    return;
  }
  const body = { entity, fact };
  const type = settingsEls.artyMemoryType.value.trim();
  if (type) body.type = type;
  if (await changeArtyMemory('add', body, settingsEls.artyMemoryAddResult, `Added to ${entity}.`)) {
    settingsEls.artyMemoryFact.value = '';
  }
};
