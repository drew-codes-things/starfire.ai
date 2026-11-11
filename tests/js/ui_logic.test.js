'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const logic = require(path.join(__dirname, '..', '..', 'static', 'uiLogic.js'));

const NOON_ON_11_NOV = new Date(2026, 10, 11, 12, 0, 0);

function daysBefore(days, hour = 9) {
  return new Date(2026, 10, 11 - days, hour, 0, 0).toISOString();
}

function fakeDocument() {
  const appended = [];
  const box = { appendChild: node => appended.push(node) };
  return {
    appended,
    getElementById: id => (id === 'toasts' ? box : null),
    createElement: () => {
      const classes = new Set();
      const node = {
        attributes: {},
        className: '',
        textContent: '',
        removed: false,
        classList: { add: name => classes.add(name), contains: name => classes.has(name) },
        setAttribute(name, value) { node.attributes[name] = value; },
        remove() { node.removed = true; },
      };
      return node;
    },
  };
}

test('chats are grouped by how long ago they were updated', () => {
  assert.equal(logic.dateGroupLabel(daysBefore(0, 8), NOON_ON_11_NOV), 'Today');
  assert.equal(logic.dateGroupLabel(daysBefore(1, 23), NOON_ON_11_NOV), 'Yesterday');
  assert.equal(logic.dateGroupLabel(daysBefore(3), NOON_ON_11_NOV), 'Previous 7 days');
  assert.equal(logic.dateGroupLabel(daysBefore(20), NOON_ON_11_NOV), 'Previous 30 days');
  assert.match(logic.dateGroupLabel(daysBefore(90), NOON_ON_11_NOV), /2026/);
});

test('a chat updated in the future still counts as today', () => {
  assert.equal(logic.dateGroupLabel(new Date(2026, 10, 12, 9).toISOString(), NOON_ON_11_NOV), 'Today');
});

test('a chat with no usable date goes under Older', () => {
  assert.equal(logic.dateGroupLabel('', NOON_ON_11_NOV), 'Older');
  assert.equal(logic.dateGroupLabel('not a date', NOON_ON_11_NOV), 'Older');
});

test('the saved theme wins, then the old light setting, then dark', () => {
  const reader = settings => key => (key in settings ? settings[key] : null);
  assert.equal(logic.themeChoiceFrom(reader({ sf_theme: 'system', sf_light: '1' })), 'system');
  assert.equal(logic.themeChoiceFrom(reader({ sf_theme: 'light' })), 'light');
  assert.equal(logic.themeChoiceFrom(reader({ sf_light: '1' })), 'light');
  assert.equal(logic.themeChoiceFrom(reader({ sf_light: '0' })), 'dark');
  assert.equal(logic.themeChoiceFrom(reader({})), 'dark');
  assert.equal(logic.themeChoiceFrom(reader({ sf_theme: 'purple' })), 'dark');
});

test('system theme follows the operating system', () => {
  assert.equal(logic.themeIsLight('system', true), true);
  assert.equal(logic.themeIsLight('system', false), false);
  assert.equal(logic.themeIsLight('light', false), true);
  assert.equal(logic.themeIsLight('dark', true), false);
});

test('a toast is announced, fades and is removed', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  global.document = fakeDocument();
  t.after(() => { delete global.document; });

  const toast = logic.showToast('Copied to clipboard');
  assert.equal(global.document.appended.length, 1);
  assert.equal(toast.textContent, 'Copied to clipboard');
  assert.equal(toast.className, 'toast');
  assert.equal(toast.attributes.role, 'status');

  t.mock.timers.tick(logic.TOAST_MS - 1);
  assert.equal(toast.classList.contains('leaving'), false);
  t.mock.timers.tick(1);
  assert.equal(toast.classList.contains('leaving'), true);
  assert.equal(toast.removed, false);
  t.mock.timers.tick(logic.TOAST_FADE_MS);
  assert.equal(toast.removed, true);
});

test('an error toast is an alert and stays longer', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  global.document = fakeDocument();
  t.after(() => { delete global.document; });

  const toast = logic.showToast('Could not save', 'error');
  assert.equal(toast.className, 'toast error');
  assert.equal(toast.attributes.role, 'alert');
  t.mock.timers.tick(logic.TOAST_MS);
  assert.equal(toast.classList.contains('leaving'), false);
  t.mock.timers.tick(logic.TOAST_ERROR_MS - logic.TOAST_MS);
  assert.equal(toast.classList.contains('leaving'), true);
});

test('an empty toast or a page with no toast area shows nothing', t => {
  global.document = fakeDocument();
  t.after(() => { delete global.document; });
  assert.equal(logic.showToast(''), null);
  global.document.getElementById = () => null;
  assert.equal(logic.showToast('Saved'), null);
});

test('removing something asks first with a danger dialog', async () => {
  const asked = [];
  const yes = await logic.confirmRemove('this preset', async question => { asked.push(question); return true; });
  assert.equal(yes, true);
  assert.deepEqual(asked, [{ title: 'Remove this preset?', text: 'This cannot be undone.', okLabel: 'Remove', danger: true }]);
});

test('cancelling the dialog means nothing is removed', async () => {
  assert.equal(await logic.confirmRemove('this memory', async () => null), false);
  assert.equal(await logic.confirmRemove('this memory', async () => false), false);
});

test('waiting approvals become cards the chat can draw', () => {
  const cards = logic.pendingApprovalCards(
    [{ id: 'abc123', command: 'df -h', cwd: '/srv', created: 1 }],
    [{ id: 'def456', path: '/srv/app.yaml', diff: '--- a\n+++ b', content: 'secret file body' }],
  );
  assert.deepEqual(cards, [
    { kind: 'shell', id: 'abc123', data: { pending_id: 'abc123', command: 'df -h', cwd: '/srv' } },
    { kind: 'edit', id: 'def456', data: { staged: true, pending_id: 'def456', path: '/srv/app.yaml', diff: '--- a\n+++ b' } },
  ]);
});

test('missing or malformed approval lists give no cards', () => {
  assert.deepEqual(logic.pendingApprovalCards(undefined, undefined), []);
  assert.deepEqual(logic.pendingApprovalCards({ not: 'a list' }, null), []);
  assert.deepEqual(logic.pendingApprovalCards([null, {}, { id: '' }, { id: 7 }], [{ path: '/x' }]), []);
});

test('a chat with its own controls uses them', () => {
  const defaults = { system_prompt: 'Default prompt', params: { temperature: 0.7 } };
  assert.deepEqual(
    logic.resolveChatControls({ system_prompt: 'Be brief', params: { top_p: 0.5 } }, defaults),
    { system_prompt: 'Be brief', params: { top_p: 0.5 } },
  );
  assert.deepEqual(
    logic.resolveChatControls({ system_prompt: '', params: {} }, defaults),
    { system_prompt: '', params: {} },
  );
});

test('an older chat with no stored controls falls back to the defaults', () => {
  const defaults = { system_prompt: 'Default prompt', params: { temperature: 0.7 } };
  const resolved = logic.resolveChatControls({ system_prompt: null, params: null }, defaults);
  assert.deepEqual(resolved, defaults);
  resolved.params.temperature = 2;
  assert.equal(defaults.params.temperature, 0.7);
  assert.deepEqual(logic.resolveChatControls(null, defaults), defaults);
});

test('only the newest assistant reply can be regenerated', () => {
  const messages = [
    { role: 'user', content: 'a' }, { role: 'assistant', content: 'b' },
    { role: 'user', content: 'c' }, { role: 'assistant', content: 'd' },
    { role: 'user', content: 'e' },
  ];
  assert.equal(logic.isLastAssistantMessage(messages, 1), false);
  assert.equal(logic.isLastAssistantMessage(messages, 3), true);
  assert.equal(logic.isLastAssistantMessage(messages, 4), false);
  assert.equal(logic.isLastAssistantMessage(messages, 9), false);
});

test('labels start with a capital letter', () => {
  assert.equal(logic.sentenceCase('web search'), 'Web search');
  assert.equal(logic.sentenceCase(''), '');
});
