'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, '..', '..', 'static');

function fakeElement() {
  const classes = new Set();
  const element = {
    children: [], dataset: {}, attributes: {}, hidden: false, value: '', title: '', textContent: '',
    classList: {
      add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name),
      toggle: (name, on) => (on === undefined ? (classes.has(name) ? classes.delete(name) : classes.add(name)) : on ? classes.add(name) : classes.delete(name)),
    },
    setAttribute(name, value) { element.attributes[name] = value; },
    addEventListener() {},
    focus() { element.focused = true; },
    scrollIntoView() {},
    appendChild(child) { element.children.push(child); return child; },
    querySelectorAll(selector) {
      const wanted = selector.split('.').filter(Boolean);
      return element.children.filter(child => wanted.every(name => child.classList.contains(name)));
    },
    querySelector(selector) { return element.querySelectorAll(selector)[0] || null; },
  };
  Object.defineProperty(element, 'className', {
    get: () => [...classes].join(' '),
    set: value => { classes.clear(); String(value).split(/\s+/).filter(Boolean).forEach(name => classes.add(name)); },
  });
  Object.defineProperty(element, 'innerHTML', { get: () => '', set: () => { element.children = []; } });
  return element;
}

const ONLINE = [
  { id: 'alpha', endpoint_id: 'e1', provider: 'openai', offline: false },
  { id: 'beta', endpoint_id: 'e1', provider: 'openai', offline: false },
  { id: 'gamma', endpoint_id: 'e1', provider: 'openai', offline: false },
];

function loadPicker() {
  const elements = {};
  const calls = { api: 0, status: [], loads: [], renders: 0 };
  const context = vm.createContext({
    document: { hidden: false, createElement: () => fakeElement() },
    window: {}, console, Date,
    setInterval: () => 0,
    module: undefined,
  });
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'uiLogic.js'), 'utf8'), context, { filename: 'uiLogic.js' });
  context.$ = id => (elements[id] = elements[id] || fakeElement());
  context.els = { modelList: { options: [] } };
  context.escapeHtml = text => String(text);
  context.icon = () => '';
  context.showToast = () => {};
  context.setStatus = (state, text) => calls.status.push([state, text]);
  context.loadModels = async options => calls.loads.push(options);
  context.closeAllMenus = () => {};
  context.answer = { models: ONLINE, auto_everyday: null };
  context.apiJson = async () => { calls.api += 1; return context.answer; };
  vm.runInContext(`
    var models = []; var busy = false; var autoEverydayModel = null; var accountModelChoice = null;
    function isAutoModel(model) { return !!model && model.id === AUTO_MODEL_ID; }
    function currentModel() { return models[0] || null; }
  `, context);
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'modelPicker.js'), 'utf8'), context, { filename: 'modelPicker.js' });
  vm.runInContext('models = ' + JSON.stringify(ONLINE), context);
  return { context, calls, elements };
}

test('the periodic refresh does nothing while a reply is running', async () => {
  const { context, calls } = loadPicker();
  vm.runInContext('busy = true', context);
  await context.refreshModelHealth();
  assert.equal(calls.api, 0);
  assert.deepEqual(calls.status, []);
});

test('a reply that starts while the refresh waits is left alone', async () => {
  const { context, calls } = loadPicker();
  context.apiJson = async () => { vm.runInContext('busy = true', context); return { models: [], auto_everyday: null }; };
  await context.refreshModelHealth();
  assert.deepEqual(calls.loads, []);
  assert.deepEqual(calls.status, []);
});

test('a changed model list is reloaded quietly, without a status line or focus', async () => {
  const { context, calls } = loadPicker();
  context.answer = { models: ONLINE.slice(0, 2), auto_everyday: null };
  await context.refreshModelHealth();
  assert.equal(calls.loads.length, 1);
  assert.equal(calls.loads[0].quiet, true);
});

test('a refresh of the open menu keeps the highlighted model', async () => {
  const { context, elements } = loadPicker();
  const menu = context.$('modelMenu');
  menu.hidden = false;
  context.$('modelSearch').value = '';
  context.renderModelMenu();
  const options = () => elements.modelMenuList.querySelectorAll('.model-option');
  options().forEach(option => option.classList.remove('highlight'));
  options().find(option => option.dataset.key === 'e1\u0000gamma').classList.add('highlight');
  await context.refreshModelHealth();
  const highlighted = options().filter(option => option.classList.contains('highlight'));
  assert.equal(highlighted.length, 1);
  assert.equal(highlighted[0].dataset.key, 'e1\u0000gamma');
});

test('an older model list answer never overwrites a newer one', async () => {
  const { context } = loadPicker();
  const answers = [];
  context.apiJson = () => new Promise(resolve => answers.push(resolve));
  const older = context.refreshModelHealth();
  const newer = context.refreshModelHealth();
  answers[1]({ models: ONLINE.map(m => ({ ...m, offline: m.id === 'beta' })), auto_everyday: null });
  await newer;
  answers[0]({ models: ONLINE, auto_everyday: null });
  await older;
  assert.equal(vm.runInContext('models', context).find(m => m.id === 'beta').offline, true);
});
