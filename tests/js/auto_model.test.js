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

test('a new chat or one whose model has gone uses the account choice, and Auto when there is none', () => {
  const accountChoice = { endpoint_id: 'e2', model: 'Arty' };
  assert.deepEqual(logic.modelChoiceToShow(null, accountChoice, LISTED), accountChoice);
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: 'e1', model: 'gone:1b' }, accountChoice, LISTED), accountChoice);
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: '', model: '' }, null, LISTED), AUTO);
  assert.deepEqual(logic.modelChoiceToShow(null, { endpoint_id: 'e1', model: 'gone:1b' }, LISTED), AUTO);
  assert.deepEqual(logic.modelChoiceToShow(null, null, []), AUTO);
});

test('a listed model is matched on its server too, so the same name on another server is not taken', () => {
  assert.deepEqual(logic.modelChoiceToShow({ endpoint_id: 'e9', model: 'general:14b' }, null, LISTED), AUTO);
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

test('Arty Auto comes first, then every model once in one flat list with no headings', () => {
  const entries = logic.modelMenuEntries(ENGINE_AND_ARTY, '');
  assert.deepEqual(ids(entries), ['auto', 'vision-vl:7b', 'general:14b', 'small:9b', 'Arty', 'own/persona']);
  assert.equal(logic.isAutoChoice(entries[0]), true);
  assert.equal(logic.AUTO_MODEL_NAME, 'Arty Auto');
  assert.equal(entries.every(m => typeof m.id === 'string' && !('heading' in m) && !('models' in m)), true);
});

test('a model reached directly and again through Arty is listed once, as the direct one', () => {
  const kept = logic.distinctModels(ENGINE_AND_ARTY);
  assert.deepEqual(kept.map(m => [m.endpoint_id, m.id]), [
    ['e1', 'vision-vl:7b'], ['e1', 'general:14b'], ['e1', 'small:9b'], ['e2', 'Arty'], ['e2', 'own/persona'],
  ]);
});

test('the same name on two servers is listed once, preferring the server that is not Arty', () => {
  const twice = [
    { id: 'Arty', endpoint_id: 'e2', own_tools: true },
    { id: 'general:14b', endpoint_id: 'e2', own_tools: false },
    { id: 'general:14b', endpoint_id: 'e1', own_tools: false },
    { id: 'general:14b', endpoint_id: 'e3', own_tools: false },
  ];
  assert.deepEqual(logic.distinctModels(twice).map(m => [m.endpoint_id, m.id]), [['e2', 'Arty'], ['e1', 'general:14b']]);
});

test('different models are never merged: namespaced names on one server, and routes with no direct model', () => {
  const engine = [
    { id: 'general:14b', endpoint_id: 'e1' },
    { id: 'maker/general:14b', endpoint_id: 'e1' },
    { id: 'other/maker/general:14b', endpoint_id: 'e2' },
  ];
  assert.deepEqual(ids(logic.distinctModels(engine)), ['general:14b', 'maker/general:14b']);
  const onlyThroughArty = [{ id: 'Arty', endpoint_id: 'e2', own_tools: true }, { id: 'arty/general:14b', endpoint_id: 'e2', own_tools: true }];
  assert.deepEqual(ids(logic.modelMenuEntries(onlyThroughArty, '')), ['auto', 'Arty', 'arty/general:14b']);
});

test('the picker offers Arty Auto and the listed models and nothing else', () => {
  const ordinary = ENGINE_AND_ARTY.filter(m => m.endpoint_id === 'e1' && m.id !== 'small:9b');
  assert.deepEqual(ids(logic.modelMenuEntries(ordinary, '')), ['auto', 'vision-vl:7b', 'general:14b']);
  assert.deepEqual(ids(logic.modelMenuEntries([], '')), ['auto']);
  assert.deepEqual(ids(logic.modelMenuEntries([], null)), ['auto']);
});

test('searching the picker filters the flat list and keeps each model once', () => {
  assert.deepEqual(ids(logic.modelMenuEntries(ENGINE_AND_ARTY, 'arty auto')), ['auto']);
  assert.deepEqual(ids(logic.modelMenuEntries(ENGINE_AND_ARTY, 'Best Model')), ['auto']);
  assert.deepEqual(ids(logic.modelMenuEntries(ENGINE_AND_ARTY, 'general')), ['general:14b']);
  assert.deepEqual(ids(logic.modelMenuEntries(ENGINE_AND_ARTY, '  VISION ')), ['vision-vl:7b']);
  assert.deepEqual(ids(logic.modelMenuEntries(ENGINE_AND_ARTY, 'arty')), ['auto', 'Arty']);
  assert.deepEqual(ids(logic.modelMenuEntries(ENGINE_AND_ARTY, 'nothing like this')), []);
});

test('the picker header says Arty Auto until a model is chosen by hand', () => {
  assert.equal(logic.pickerMode(null), logic.PICKER_MODE_AUTO);
  assert.equal(logic.pickerMode(logic.autoModelEntry()), logic.PICKER_MODE_AUTO);
  assert.equal(logic.pickerMode({ endpoint_id: 'e1', id: 'general:14b' }), logic.PICKER_MODE_CHOSEN);
  assert.notEqual(logic.PICKER_MODE_AUTO, logic.PICKER_MODE_CHOSEN);
});
