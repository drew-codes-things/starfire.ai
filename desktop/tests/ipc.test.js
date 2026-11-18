const test = require('node:test');
const assert = require('node:assert/strict');
const { registerIpc, CHANNELS } = require('../src/ipc');

const SECRET_TOKEN = 'abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE';

function setup({ local = true, connectResult = { ok: true, origin: 'https://a.example.com', version: '1' } } = {}) {
  const handlers = new Map();
  const ipcMain = { handle: (channel, handler) => handlers.set(channel, handler) };
  const calls = [];
  const controller = {
    get: () => ({ shortcut: 'Ctrl+Shift+Space', artyLink: true, artyToken: SECRET_TOKEN }),
    update: (patch) => { calls.push(['update', patch]); return { ok: true, settings: { artyToken: SECRET_TOKEN, artyLink: true } }; },
    connect: async (request) => { calls.push(['connect', request]); return connectResult; },
  };
  const actions = {
    shortcutProblem: () => null,
    submitQuickAsk: async (text) => { calls.push(['ask', text]); return { ok: true }; },
    hideQuickAsk: () => calls.push(['hide']),
    openConnect: (mode) => calls.push(['openConnect', mode]),
    retry: () => calls.push(['retry']),
    closeConnect: () => calls.push(['closeConnect']),
    connected: () => calls.push(['connected']),
    linkStatus: async () => { calls.push(['linkStatus']); return { state: 'connected', paired: true }; },
    linkPair: async (code) => { calls.push(['linkPair', code]); return { ok: true }; },
    linkUnpair: async () => { calls.push(['linkUnpair']); return { ok: true }; },
    linkTry: async (sender) => { calls.push(['linkTry', sender]); return { ok: true, result: 'x' }; },
  };
  registerIpc({ ipcMain, controller, isLocalSender: () => local, isSettingsSender: () => local, actions, describeKey: (event) => ({ ok: true, accelerator: event.code }) });
  return { handlers, calls };
}

test('every channel gets a handler', () => {
  const { handlers } = setup();
  for (const channel of Object.values(CHANNELS)) assert.ok(handlers.has(channel), channel);
});

test('calls from anything but the app\'s own pages are refused', async () => {
  const { handlers, calls } = setup({ local: false });
  for (const [channel, handler] of handlers) {
    await assert.rejects(() => handler({}, {}), /Refused/, channel);
  }
  assert.equal(calls.length, 0);
});

test('connect passes only the address and a strict boolean, and reports success to the app', async () => {
  const { handlers, calls } = setup();
  const result = await handlers.get(CHANNELS.connect)({}, { address: 'a.example.com', allowInsecure: 'true', extra: 1 });
  assert.equal(result.ok, true);
  assert.deepEqual(calls, [['connect', { address: 'a.example.com', allowInsecure: false }], ['connected']]);
});

test('a failed connect does not report success', async () => {
  const { handlers, calls } = setup({ connectResult: { ok: false, error: 'nope' } });
  const result = await handlers.get(CHANNELS.connect)({}, { address: 'x', allowInsecure: true });
  assert.equal(result.ok, false);
  assert.deepEqual(calls.map((call) => call[0]), ['connect']);
});

test('key events are reduced before they reach the shortcut code', async () => {
  const { handlers } = setup();
  const described = await handlers.get(CHANNELS.describeKey)({}, { code: 'KeyK', ctrlKey: true, evil: {} });
  assert.deepEqual(described, { ok: true, accelerator: 'KeyK' });
});

test('the pages never receive the Arty token, only whether it is paired', async () => {
  const { handlers } = setup();
  const read = await handlers.get(CHANNELS.getSettings)({});
  const saved = await handlers.get(CHANNELS.saveSettings)({}, { artyLink: true });
  for (const answer of [read, saved]) {
    assert.equal(JSON.stringify(answer).includes(SECRET_TOKEN), false);
    assert.equal(answer.settings.artyPaired, true);
  }
});

test('the link channels hand plain values to the link', async () => {
  const { handlers, calls } = setup();
  assert.deepEqual(await handlers.get(CHANNELS.linkStatus)({}), { state: 'connected', paired: true });
  await handlers.get(CHANNELS.linkPair)({}, { code: '123456', extra: 1 });
  await handlers.get(CHANNELS.linkPair)({}, null);
  await handlers.get(CHANNELS.linkUnpair)({});
  await handlers.get(CHANNELS.linkTry)({ sender: 'settings' }, { tool: 'computer_apps', arguments: { a: 1 }, extra: 1 });
  await handlers.get(CHANNELS.linkTry)({ sender: 'settings' }, 'nonsense');
  assert.deepEqual(calls, [
    ['linkStatus'],
    ['linkPair', '123456'],
    ['linkPair', undefined],
    ['linkUnpair'],
    ['linkTry', 'settings'],
    ['linkTry', 'settings'],
  ]);
});
