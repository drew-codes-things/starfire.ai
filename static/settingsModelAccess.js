'use strict';

const modelAccessEls = {
  modal: document.getElementById('modelAccessModal'), title: document.getElementById('modelAccessTitle'),
  help: document.getElementById('modelAccessHelp'), all: document.getElementById('modelAccessAll'),
  search: document.getElementById('modelAccessSearch'),
  selectAll: document.getElementById('modelAccessSelectAll'), selectNone: document.getElementById('modelAccessSelectNone'),
  list: document.getElementById('modelAccessList'), kept: document.getElementById('modelAccessKept'),
  count: document.getElementById('modelAccessCount'), result: document.getElementById('modelAccessResult'),
  cancel: document.getElementById('modelAccessCancel'), save: document.getElementById('modelAccessSave'),
  patternRow: document.getElementById('modelAccessPatternRow'), pattern: document.getElementById('modelAccessPattern'), addPattern: document.getElementById('modelAccessAddPattern'),
};

const MODEL_ACCESS_HELP = 'Tick the models this account may use and press Save; it takes effect at once. ' +
  'Access is by model name: ticking a name allows it on every connection that lists that name. ' +
  '"All models" lets the account use every model except the restricted ones, which you tick one by one. ' +
  'With All models on every normal model shows ticked; untick one, or tick a restricted one, and All models switches off and the ticks become the saved list. ' +
  'A pattern such as llama* allows every unrestricted model it matches, now and later. ' +
  'Admins can always use every model.';

let modelAccessSession = null;

function modelAccessNote(text, kind) {
  modelAccessEls.result.textContent = text;
  modelAccessEls.result.className = 'settings-hint' + (kind ? ' ' + kind : '');
}

function modelAccessVisible() {
  const query = modelAccessEls.search.value;
  return modelAccessSession.view.available.filter(model => accessMatchesSearch(model, query));
}

function modelAccessByPattern(model) {
  return coveredByKeptPattern(model, modelAccessSession.kept);
}

function renderModelAccessCount() {
  const { everything, ticked, view } = modelAccessSession;
  const names = new Set(view.available.filter(m => modelAccessByPattern(m) && !ticked.has(m.id)).map(m => m.id));
  modelAccessEls.count.textContent = modelAccessCount(everything, ticked, names.size);
}

function applyModelAccessState(next) {
  modelAccessSession.everything = next.everything;
  modelAccessSession.ticked = next.ticked;
}

function tickEverywhere(modelId, ticked) {
  const wasEverything = modelAccessSession.everything;
  applyModelAccessState(toggleModelTick(modelAccessSession, modelAccessSession.view.available, modelId, ticked));
  if (wasEverything !== modelAccessSession.everything) {
    modelAccessNote('');
    renderModelAccess();
    return;
  }
  for (const box of modelAccessEls.list.querySelectorAll('input[type="checkbox"]')) {
    if (box.dataset.modelId === modelId && !box.disabled) box.checked = ticked;
  }
  renderModelAccessCount();
}

function modelAccessRow(model) {
  const byPattern = modelAccessByPattern(model) && !modelAccessSession.everything;
  const li = document.createElement('li');
  li.className = 'endpoint-row';
  const label = document.createElement('label');
  label.className = 'theme-toggle';
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.dataset.modelId = model.id;
  const state = accessBoxState(modelAccessSession, model, byPattern);
  box.checked = state.checked;
  box.disabled = state.disabled;
  box.onchange = () => tickEverywhere(model.id, box.checked);
  const name = document.createElement('span');
  name.textContent = model.id;
  label.append(box, name);
  for (const text of accessModelTags(model, byPattern)) label.append(modelAccessTag(text));
  li.appendChild(label);
  return li;
}

function modelAccessTag(text) {
  const tag = document.createElement('span');
  tag.className = 'model-access-tag';
  tag.textContent = text;
  return tag;
}

function modelAccessSectionHeading(title) {
  const heading = document.createElement('div');
  heading.className = 'model-access-section';
  heading.textContent = title;
  return heading;
}

function modelAccessGroupNodes(group) {
  const heading = document.createElement('div');
  heading.className = 'model-access-group';
  heading.textContent = group.connection;
  const ul = document.createElement('ul');
  ul.className = 'endpoint-list';
  for (const model of group.models) ul.appendChild(modelAccessRow(model));
  return [heading, ul];
}

function renderModelAccessList() {
  const sections = accessSections(modelAccessVisible());
  modelAccessEls.list.textContent = '';
  if (!sections.length) {
    const empty = document.createElement('p');
    empty.className = 'settings-hint';
    empty.textContent = modelAccessSession.view.available.length ? 'No model matches that search.' : 'starfire does not see any models right now.';
    modelAccessEls.list.appendChild(empty);
  }
  for (const section of sections) {
    if (section.title) modelAccessEls.list.appendChild(modelAccessSectionHeading(section.title));
    for (const group of section.groups) modelAccessEls.list.append(...modelAccessGroupNodes(group));
  }
  renderModelAccessCount();
}

function renderModelAccessKept() {
  modelAccessEls.kept.textContent = '';
  const kept = modelAccessSession.kept;
  if (!kept.length) return;
  const heading = document.createElement('div');
  heading.className = 'model-access-group';
  heading.textContent = 'Patterns, and saved names that match no listed model (kept when you save)';
  const ul = document.createElement('ul');
  ul.className = 'endpoint-list';
  for (const entry of kept) {
    const li = document.createElement('li');
    li.className = 'endpoint-row';
    const name = document.createElement('span');
    name.className = 'endpoint-label';
    name.textContent = entry;
    const remove = document.createElement('button');
    remove.className = 'endpoint-remove';
    remove.type = 'button';
    remove.textContent = '×';
    remove.title = `Forget ${entry}`;
    remove.setAttribute('aria-label', `Forget ${entry}`);
    remove.onclick = () => {
      modelAccessSession.kept = modelAccessSession.kept.filter(item => item !== entry);
      renderModelAccessKept();
      renderModelAccessList();
    };
    li.append(name, remove);
    ul.appendChild(li);
  }
  modelAccessEls.kept.append(heading, ul);
}

function renderModelAccess() {
  modelAccessEls.all.checked = modelAccessSession.everything;
  const showKept = keptControlsShown(modelAccessSession);
  modelAccessEls.kept.hidden = !showKept;
  modelAccessEls.patternRow.hidden = !showKept;
  renderModelAccessList();
  renderModelAccessKept();
}

function closeModelAccess() {
  modelAccessEls.modal.hidden = true;
  modelAccessSession = null;
}

function addModelAccessPattern() {
  const entry = modelAccessEls.pattern.value.trim();
  if (!entry) return;
  if (!modelAccessSession.kept.includes(entry)) modelAccessSession.kept = [...modelAccessSession.kept, entry];
  modelAccessEls.pattern.value = '';
  renderModelAccessKept();
  renderModelAccessList();
}

async function saveModelAccess() {
  const { everything, ticked, view, kept, url, request, report, who } = modelAccessSession;
  const models = modelListToSave({ everything, ticked, available: view.available, kept });
  const tooMany = tooManyModelsNote(models, view.max_models);
  if (tooMany) {
    modelAccessNote(tooMany, 'error');
    return;
  }
  modelAccessEls.save.disabled = true;
  modelAccessNote('saving…');
  try {
    const saved = await request('PUT', url, { models });
    report(`${who} may use: ${modelAccessSummary(saved.models)}`, 'ok');
    closeModelAccess();
  } catch (e) {
    modelAccessNote(e.message, 'error');
  } finally {
    modelAccessEls.save.disabled = false;
  }
}

async function openModelAccess({ title, url, request, report, who }) {
  try {
    const view = await request('GET', url);
    modelAccessSession = {
      view, url, request, report, who, everything: view.models === null,
      ticked: ticksFromAccess(view), kept: [...view.kept],
    };
  } catch (e) {
    report(e.message, 'error');
    return;
  }
  modelAccessEls.title.textContent = title;
  modelAccessEls.help.textContent = MODEL_ACCESS_HELP;
  modelAccessEls.search.value = '';
  modelAccessEls.pattern.value = '';
  modelAccessNote('');
  renderModelAccess();
  modelAccessEls.modal.hidden = false;
  modelAccessEls.all.focus();
}

modelAccessEls.all.onchange = () => {
  const next = switchEverything(modelAccessSession, modelAccessSession.view.available, modelAccessEls.all.checked, modelAccessSession.kept);
  applyModelAccessState(next);
  modelAccessNote(droppedRestrictedNote(next.dropped));
  renderModelAccess();
};
modelAccessEls.search.oninput = () => renderModelAccessList();
modelAccessEls.selectAll.onclick = () => {
  applyModelAccessState(selectShown(modelAccessSession, modelAccessSession.view.available, modelAccessVisible(), true));
  renderModelAccess();
};
modelAccessEls.selectNone.onclick = () => {
  applyModelAccessState(selectShown(modelAccessSession, modelAccessSession.view.available, modelAccessVisible(), false));
  renderModelAccess();
};
modelAccessEls.addPattern.onclick = addModelAccessPattern;
modelAccessEls.pattern.onkeydown = e => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  addModelAccessPattern();
};
modelAccessEls.save.onclick = saveModelAccess;
modelAccessEls.cancel.onclick = closeModelAccess;
modelAccessEls.modal.onkeydown = closeOnEscape(closeModelAccess);
