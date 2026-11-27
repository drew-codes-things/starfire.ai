'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const logic = require(path.join(__dirname, '..', '..', 'static', 'uiLogic.js'));

const LISTED = [
  { id: 'vision-vl:7b', endpoint_id: 'e1' },
  { id: 'general:14b', endpoint_id: 'e1' },
  { id: 'Arty', endpoint_id: 'e2' },
];
const AUTO = { endpoint_id: 'auto', model: 'auto' };

test('a chat shows the model it was using, not the first model in the list', () => {
  const chat = { endpoint_id: 'e1', model: 'general:14b' };
  assert.deepEqual(logic.modelChoiceToShow(chat, AUTO, LISTED), { endpoint_id: 'e1', model: 'general:14b' });
});

test('a chat that was on Auto stays on Auto even when the account last picked a model', () => {
  const accountChoice = { endpoint_id: 'e2', model: 'Arty' };
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: 'auto', model: 'auto' }, accountChoice, LISTED), AUTO);
});

const FIRST_LISTED = { endpoint_id: 'e1', model: 'vision-vl:7b' };

test('a new chat or one whose model has gone uses the account choice, and the first listed model when there is none', () => {
  const accountChoice = { endpoint_id: 'e2', model: 'Arty' };
  assert.deepEqual(logic.modelChoiceToShow(null, accountChoice, LISTED), accountChoice);
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: 'e1', model: 'gone:1b' }, accountChoice, LISTED), accountChoice);
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: '', model: '' }, null, LISTED), FIRST_LISTED);
  assert.deepEqual(logic.modelChoiceToShow(null, { endpoint_id: 'e1', model: 'gone:1b' }, LISTED), FIRST_LISTED);
  assert.deepEqual(logic.modelChoiceToShow(null, null, []), AUTO);
});

test('a listed model is matched on its server too, so the same name on another server is not taken', () => {
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: 'e9', model: 'general:14b' }, null, LISTED), FIRST_LISTED);
});

test('a picker option written as id instead of model is understood', () => {
  assert.equal(logic.isAutoChoice({ endpoint_id: 'auto', id: 'auto' }), true);
  assert.equal(logic.isAutoChoice({ endpoint_id: 'e1', id: 'auto' }), false);
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: 'e1', id: 'general:14b' }, null, LISTED),
    { endpoint_id: 'e1', model: 'general:14b' });
});

test('a reply Arty Auto answered names the model and the role it was picked for', () => {
  assert.equal(logic.replyModelLabel({ model: 'coder:7b', auto: 'code' }), 'coder:7b · Arty Auto: code');
  assert.equal(logic.replyModelLabel({ model: 'general:14b' }), 'general:14b');
  assert.equal(logic.autoPickLabel('vision-vl:7b', 'vision'), 'vision-vl:7b · Arty Auto: vision');
});

const ENGINE_AND_ARTY = [
  { id: 'vision-vl:7b', endpoint_id: 'e1', own_tools: false },
  { id: 'general:14b', endpoint_id: 'e1', own_tools: false },
  { id: 'small:9b', endpoint_id: 'e1', own_tools: false },
  { id: 'Arty', endpoint_id: 'e2', own_tools: true },
  { id: 'arty/vision-vl:7b', endpoint_id: 'e2', own_tools: true },
  { id: 'arty/general:14b', endpoint_id: 'e2', own_tools: true },
  { id: 'arty/small:9b', endpoint_id: 'e2', own_tools: true },
  { id: 'solo/general:14b', endpoint_id: 'e2', own_tools: false },
  { id: 'own/persona', endpoint_id: 'e2', own_tools: false },
];

function ids(entries) {
  return entries.map(m => m.id);
}

test('a model reached directly and again through Arty is listed once, as the direct one', () => {
  const kept = logic.distinctModels(ENGINE_AND_ARTY);
  assert.deepEqual(kept.map(m => [m.endpoint_id, m.id]), [
    ['e1', 'vision-vl:7b'], ['e1', 'general:14b'], ['e1', 'small:9b'], ['e2', 'Arty'], ['e2', 'own/persona'],
  ]);
});

test('a model Arty also offers is listed once as the direct one, and the same name on two other servers is listed twice and told apart', () => {
  const twice = [
    { id: 'Arty', endpoint_id: 'e2', own_tools: true },
    { id: 'general:14b', endpoint_id: 'e2', own_tools: false },
    { id: 'general:14b', endpoint_id: 'e1', own_tools: false },
    { id: 'general:14b', endpoint_id: 'e3', own_tools: false },
  ];
  assert.deepEqual(logic.distinctModels(twice).map(m => [m.endpoint_id, m.id, m.server_note]), [
    ['e2', 'Arty', undefined], ['e1', 'general:14b', 'server 2'], ['e3', 'general:14b', 'server 3'],
  ]);
});

test('a cloud server listed first does not hide the same name on the local engine', () => {
  const cloudFirst = [
    { id: 'general:14b', endpoint_id: 'cloud', own_tools: false },
    { id: 'evil/general:14b', endpoint_id: 'cloud', own_tools: false },
    { id: 'general:14b', endpoint_id: 'engine', own_tools: false },
  ];
  const entries = logic.modelMenuEntries(cloudFirst, '', logic.PICKER_MODE_CHOSEN);
  assert.deepEqual(entries.map(m => [m.endpoint_id, m.id]), [
    ['cloud', 'general:14b'], ['cloud', 'evil/general:14b'], ['engine', 'general:14b'],
  ]);
  const notes = entries.filter(m => m.id === 'general:14b').map(m => m.server_note);
  assert.deepEqual(notes, ['server 1', 'server 2']);
  assert.equal(entries.find(m => m.id === 'evil/general:14b').server_note, undefined);
});

test('different models are never merged: namespaced names on one server, and routes with no direct model', () => {
  const engine = [
    { id: 'general:14b', endpoint_id: 'e1' },
    { id: 'maker/general:14b', endpoint_id: 'e1' },
    { id: 'other/maker/general:14b', endpoint_id: 'e2' },
  ];
  assert.deepEqual(ids(logic.distinctModels(engine)), ['general:14b', 'maker/general:14b', 'other/maker/general:14b']);
  const onlyThroughArty = [{ id: 'Arty', endpoint_id: 'e2', own_tools: true }, { id: 'arty/general:14b', endpoint_id: 'e2', own_tools: true }];
  assert.deepEqual(ids(logic.modelMenuEntries(onlyThroughArty, '', logic.PICKER_MODE_ARTY)), ['Arty', 'auto']);
  assert.deepEqual(ids(logic.modelMenuEntries(onlyThroughArty, '', logic.PICKER_MODE_CHOSEN)), ['arty/general:14b']);
});

const BIG = 'hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_XS';
const SIDE = 'hf.co/maker/Qwen3.6-35B-A3B-Side-GGUF:IQ4_XS';
const REMOTE_ONLY = [
  { id: 'remote-model-a', endpoint_id: 'remote-a', remote: true, label: 'Remote Model A' },
  { id: 'remote-model-b', endpoint_id: 'remote-a', remote: true, label: 'Remote Side B' },
];
const DREW = [
  { id: BIG, endpoint_id: 'engine', label: 'Qwen3.6 35B' },
  { id: 'qwen3:14b', endpoint_id: 'engine' },
  { id: 'Arty', endpoint_id: 'brain', own_tools: true },
  { id: 'arty/' + BIG, endpoint_id: 'brain', own_tools: true, label: 'Qwen3.6 35B' },
  { id: 'arty/' + SIDE, endpoint_id: 'brain', own_tools: true, private: true, label: 'Qwen3.6 35B Side' },
  { id: 'arty/qwen3:14b', endpoint_id: 'brain', own_tools: true },
  ...REMOTE_ONLY,
];
const NOTHING_CHOSEN = { endpoint_id: '', model: '' };

function shownNames(entries) {
  return entries.map(m => m.heading ? '# ' + m.heading : logic.modelDisplayName(m));
}

test('the picker has two tabs, Arty and Choose a model', () => {
  assert.deepEqual(logic.PICKER_TABS.map(tab => tab.name), ['Arty', 'Choose a model']);
  assert.deepEqual(logic.PICKER_TABS.map(tab => tab.mode), [logic.PICKER_MODE_ARTY, logic.PICKER_MODE_CHOSEN]);
  const page = require('node:fs').readFileSync(path.join(__dirname, '..', '..', 'static', 'index.html'), 'utf8');
  assert.match(page, /id="modelModeArty"[^>]*>Arty</);
  assert.match(page, /id="modelModeChosen"[^>]*>Choose a model</);
});

test('the Arty tab shows Arty, then Arty Auto, then the Remote section', () => {
  assert.deepEqual(shownNames(logic.modelMenuEntries(DREW, '', logic.PICKER_MODE_ARTY)),
    ['Arty', 'Arty Auto', '# Remote', 'Remote Model A', 'Remote Side B']);
});

test('the Choose a model tab shows the local models by name, then the same Remote section', () => {
  const entries = logic.modelMenuEntries(DREW, '', logic.PICKER_MODE_CHOSEN);
  assert.deepEqual(shownNames(entries),
    ['Qwen3.6 35B', 'Qwen3.6 35B Side', 'qwen3:14b', '# Remote', 'Remote Model A', 'Remote Side B']);
  assert.deepEqual(entries.slice(0, 3).map(m => [m.endpoint_id, m.id]), [['engine', BIG], ['brain', 'arty/' + SIDE], ['engine', 'qwen3:14b']]);
});

test('no picker entry shows a raw download id', () => {
  const unlabelled = [{ id: 'hf.co/maker/Some-Model-9B-GGUF:Q4_K_M', endpoint_id: 'engine' }, { id: 'arty/hf.co/maker/Other-7B-GGUF:Q8_0', endpoint_id: 'brain', own_tools: true }];
  for (const mode of [logic.PICKER_MODE_ARTY, logic.PICKER_MODE_CHOSEN]) {
    for (const name of shownNames(logic.modelMenuEntries([...DREW, ...unlabelled], '', mode))) assert.doesNotMatch(name, /hf\.co\//);
  }
  assert.equal(logic.modelDisplayName(unlabelled[0]), 'Some-Model-9B');
  assert.equal(logic.modelDisplayName(unlabelled[1]), 'arty/Other-7B');
  assert.equal(logic.modelDisplayName(logic.autoModelEntry()), 'Arty Auto');
});

test('Arty is the default until the account picks something, and a kept Auto stays Auto', () => {
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, DREW), { endpoint_id: 'brain', model: 'Arty' });
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: 'engine', model: 'gone:9b' }, null, DREW), { endpoint_id: 'brain', model: 'Arty' });
  assert.deepEqual(logic.modelChoiceToShow(null, AUTO, DREW), AUTO);
  assert.deepEqual(logic.modelChoiceToShow(null, { endpoint_id: 'engine', model: 'qwen3:14b' }, DREW), { endpoint_id: 'engine', model: 'qwen3:14b' });
});

test('an account that may only use the remote models sees no Arty tab or Arty Auto, only the Remote section', () => {
  for (const mode of [logic.PICKER_MODE_ARTY, logic.PICKER_MODE_CHOSEN]) {
    assert.deepEqual(shownNames(logic.modelMenuEntries(REMOTE_ONLY, '', mode)), ['# Remote', 'Remote Model A', 'Remote Side B']);
  }
  assert.equal(logic.offersArty(REMOTE_ONLY), false);
  assert.equal(logic.offersArty([{ id: 'nomic-embed-text:latest', endpoint_id: 'engine' }, ...REMOTE_ONLY]), false);
  assert.equal(logic.offersArty([{ id: 'remote/starfire/model-a', endpoint_id: 'brain' }]), false);
  assert.equal(logic.offersArty([{ id: 'qwen3:14b', endpoint_id: 'engine' }, ...REMOTE_ONLY]), true);
  assert.equal(logic.offersArty(DREW), true);
  assert.equal(logic.pickerMode(null, REMOTE_ONLY), logic.PICKER_MODE_CHOSEN);
  assert.deepEqual(logic.modelChoiceToShow(null, AUTO, REMOTE_ONLY), { endpoint_id: 'remote-a', model: 'remote-model-a' });
  const fs = require('node:fs');
  const source = name => fs.readFileSync(path.join(__dirname, '..', '..', 'static', name), 'utf8');
  assert.match(source('chatSend.js'), /if \(offersArty\(models\)\) els\.modelList\.appendChild\(modelOption\(autoModelEntry\(\)\)\);/);
  assert.match(source('modelPicker.js'), /\$\('modelModeArty'\)\.hidden = !offersArty\(models\);/);
  assert.match(source('modelPicker.js'), /pickerMode\(current, models\)/);
});

test('an account without Arty starts on the first model it may use, its first Remote model', () => {
  const first = { endpoint_id: 'remote-a', model: 'remote-model-a' };
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, REMOTE_ONLY), first);
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: 'engine', model: 'gone:9b' }, null, REMOTE_ONLY), first);
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, [{ id: 'qwen3:14b', endpoint_id: 'engine' }]),
    { endpoint_id: 'engine', model: 'qwen3:14b' });
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, []), AUTO);
});

test('an account without Arty starts on its first local model and on a Remote model only when it has nothing local', () => {
  const local = { id: 'qwen3:14b', endpoint_id: 'engine' };
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, [...REMOTE_ONLY, local]),
    { endpoint_id: 'engine', model: 'qwen3:14b' });
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, [local, ...REMOTE_ONLY]),
    { endpoint_id: 'engine', model: 'qwen3:14b' });
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, REMOTE_ONLY),
    { endpoint_id: 'remote-a', model: 'remote-model-a' });
});

test('the picker opens on the tab of the model in use', () => {
  assert.equal(logic.pickerMode(null), logic.PICKER_MODE_ARTY);
  assert.equal(logic.pickerMode(logic.autoModelEntry()), logic.PICKER_MODE_ARTY);
  assert.equal(logic.pickerMode({ endpoint_id: 'brain', id: 'Arty', own_tools: true }), logic.PICKER_MODE_ARTY);
  assert.equal(logic.pickerMode({ endpoint_id: 'engine', id: 'qwen3:14b' }), logic.PICKER_MODE_CHOSEN);
});

test('searching finds models by their shown name within the open tab', () => {
  assert.deepEqual(shownNames(logic.modelMenuEntries(DREW, 'side', logic.PICKER_MODE_CHOSEN)),
    ['Qwen3.6 35B Side', '# Remote', 'Remote Side B']);
  assert.deepEqual(shownNames(logic.modelMenuEntries(DREW, 'arty auto', logic.PICKER_MODE_ARTY)), ['Arty Auto']);
  assert.deepEqual(shownNames(logic.modelMenuEntries(DREW, 'nothing like this', logic.PICKER_MODE_CHOSEN)), []);
});

test('the Auto settings name the default and coding models first and list each model once by its shown name', () => {
  const source = require('node:fs').readFileSync(path.join(__dirname, '..', '..', 'static', 'settings.js'), 'utf8');
  assert.match(source, /chat: 'Default model',\s*code: 'Coding model',/);
  const listed = [
    { id: BIG, endpoint_id: 'engine', label: 'Qwen3.6 35B' },
    { id: 'arty/' + BIG, endpoint_id: 'brain', own_tools: true, label: 'Qwen3.6 35B' },
    { id: 'qwen3:14b', endpoint_id: 'engine' },
    { id: 'qwen3:14b', endpoint_id: 'other' },
    ...REMOTE_ONLY,
  ];
  assert.deepEqual(logic.autoRoleChoices(listed), [
    { id: BIG, text: 'Qwen3.6 35B' },
    { id: 'arty/' + BIG, text: 'Qwen3.6 35B (through Arty)' },
    { id: 'qwen3:14b', text: 'qwen3:14b' },
    { id: 'remote-model-a', text: 'Remote Model A (remote)' },
    { id: 'remote-model-b', text: 'Remote Side B (remote)' },
  ]);
  assert.deepEqual(logic.autoRoleChoices(null), []);
});

test('the Default and Coding model lists offer only the local models the server allows', () => {
  const listed = [
    { id: BIG, endpoint_id: 'engine', label: 'Qwen3.6 35B' },
    { id: 'arty/' + BIG, endpoint_id: 'brain', own_tools: true, label: 'Qwen3.6 35B' },
    { id: 'qwen3:14b', endpoint_id: 'engine' },
    ...REMOTE_ONLY,
  ];
  assert.deepEqual(logic.autoRoleChoices(listed, [BIG, 'qwen3:14b']), [
    { id: BIG, text: 'Qwen3.6 35B' },
    { id: 'qwen3:14b', text: 'qwen3:14b' },
  ]);
  assert.deepEqual(logic.autoRoleChoices(listed, null).length, 5);
  const source = require('node:fs').readFileSync(path.join(__dirname, '..', '..', 'static', 'settings.js'), 'utf8');
  assert.match(source, /autoRoleChoices\(onlineModels\(\), LOCAL_ONLY_ROLES\.includes\(role\) \? view\.local_models : null\)/);
});

test('a remote/ model on a home connection and an embedding model never win the starting model over a local chat model', () => {
  const engine = id => ({ id, endpoint_id: 'engine' });
  const brainListed = id => ({ id, endpoint_id: 'brain' });
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, [brainListed('remote/starfire/model-a'), engine('qwen3:14b')]),
    { endpoint_id: 'engine', model: 'qwen3:14b' });
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, [engine('nomic-embed-text:latest'), ...REMOTE_ONLY]),
    { endpoint_id: 'remote-a', model: 'remote-model-a' });
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, [engine('Nomic-Embed-Text:latest')]), AUTO);
  assert.deepEqual(logic.modelChoiceToShow(null, NOTHING_CHOSEN, [brainListed('remote/starfire/model-a')]),
    { endpoint_id: 'brain', model: 'remote/starfire/model-a' });
});
