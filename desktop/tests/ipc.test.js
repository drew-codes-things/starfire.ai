const test = require('node:test');
const assert = require('node:assert/strict');
const { registerIpc, CHANNELS } = require('../src/ipc');

function setup({ local = true, connectResult = { ok: true, origin: 'https://a.example.com', version: '1' } } = {}) {
  const handlers = new Map();
  const ipcMain = { handle: (channel, handler) => handlers.set(channel, handler) };
  const calls = [];
  const controller = {
    get: () => ({ shortcut: 'Ctrl+Shift+Space' }),
    update: (patch) => { calls.push(['update', patch]); return { ok: true, settings: {} }; },
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
  };
  registerIpc({ ipcMain, controller, isLocalSender: () => local, actions, describeKey: (event) => ({ ok: true, accelerator: event.code }) });
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
