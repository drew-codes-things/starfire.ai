const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createSettingsController } = require('../src/controller');
const { DEFAULT_SETTINGS, loadSettings } = require('../src/settings');

function fakeGlobalShortcut({ taken = [] } = {}) {
  const registered = new Map();
  return {
    registered,
    register(accelerator, handler) {
      if (taken.includes(accelerator)) return false;
      registered.set(accelerator, handler);
      return true;
    },
    unregister(accelerator) { registered.delete(accelerator); },
    isRegistered(accelerator) { return registered.has(accelerator); },
  };
}

function build({ taken = [], initial = {}, check, setAutostart } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'sfd-controller-'));
  const file = path.join(dir, 'config.json');
  const globalShortcut = fakeGlobalShortcut({ taken });
  const changes = [];
  const autostart = [];
  const controller = createSettingsController({
    file,
    initial: { ...DEFAULT_SETTINGS, ...initial },
    globalShortcut,
    onQuickAsk: () => {},
    setAutostart: setAutostart || ((enabled) => autostart.push(enabled)),
    onChange: (next, previous) => changes.push({ next, previous }),
    check,
  });
  return { controller, globalShortcut, file, changes, autostart, dir };
}

test('start registers the saved shortcut', () => {
  const { controller, globalShortcut } = build();
  assert.equal(controller.start().ok, true);
  assert.deepEqual([...globalShortcut.registered.keys()], ['Ctrl+Shift+Space']);
  assert.equal(controller.shortcutProblem(), null);
});

test('start reports a taken shortcut and remembers the problem', () => {
  const { controller } = build({ taken: ['Ctrl+Shift+Space'] });
  const result = controller.start();
  assert.equal(result.ok, false);
  assert.match(controller.shortcutProblem(), /already in use/);
});

test('changing the shortcut registers it, saves it and tells the app', () => {
  const { controller, globalShortcut, file, changes } = build();
  controller.start();
  const result = controller.update({ shortcut: 'ctrl+alt+k' });
  assert.equal(result.ok, true);
  assert.equal(result.settings.shortcut, 'Ctrl+Alt+K');
  assert.deepEqual([...globalShortcut.registered.keys()], ['Ctrl+Alt+K']);
  assert.equal(loadSettings(file).settings.shortcut, 'Ctrl+Alt+K');
  assert.equal(changes.length, 1);
  assert.equal(changes[0].previous.shortcut, 'Ctrl+Shift+Space');
});

test('a shortcut that cannot be registered changes nothing', () => {
  const { controller, globalShortcut, file, changes } = build({ taken: ['Ctrl+Alt+K'] });
  controller.start();
  const result = controller.update({ shortcut: 'Ctrl+Alt+K', notifications: false });
  assert.equal(result.ok, false);
  assert.deepEqual([...globalShortcut.registered.keys()], ['Ctrl+Shift+Space']);
  assert.equal(controller.get().shortcut, 'Ctrl+Shift+Space');
  assert.equal(controller.get().notifications, true);
  assert.equal(fs.existsSync(file), false);
  assert.equal(changes.length, 0);
});

test('an unrelated change still saves when the shortcut is taken by another program', () => {
  const { controller, file } = build({ taken: ['Ctrl+Shift+Space'] });
  controller.start();
  const result = controller.update({ notifications: false });
  assert.equal(result.ok, true);
  assert.equal(loadSettings(file).settings.notifications, false);
});

test('fixing a taken shortcut clears the problem', () => {
  const { controller } = build({ taken: ['Ctrl+Shift+Space'] });
  controller.start();
  assert.ok(controller.shortcutProblem());
  assert.equal(controller.update({ shortcut: 'Ctrl+Alt+K' }).ok, true);
  assert.equal(controller.shortcutProblem(), null);
});

test('start with the PC calls the autostart hook only when the value changes', () => {
  const { controller, autostart } = build();
  controller.update({ startWithSystem: true });
  controller.update({ startWithSystem: true });
  controller.update({ startWithSystem: false });
  assert.deepEqual(autostart, [true, false]);
});

test('an autostart failure is reported and nothing is saved', () => {
  const { controller, file } = build({ setAutostart: () => { throw new Error('read-only folder'); } });
  const result = controller.update({ startWithSystem: true });
  assert.equal(result.ok, false);
  assert.match(result.error, /read-only folder/);
  assert.equal(controller.get().startWithSystem, false);
  assert.equal(fs.existsSync(file), false);
});

test('a taken shortcut undoes an autostart change made in the same update', () => {
  const { controller, autostart } = build({ taken: ['Ctrl+Alt+K'] });
  controller.start();
  const result = controller.update({ startWithSystem: true, shortcut: 'Ctrl+Alt+K' });
  assert.equal(result.ok, false);
  assert.deepEqual(autostart, [true, false]);
});

test('connect checks the address and then saves it', async () => {
  const checked = [];
  const { controller, file, changes } = build({ check: async (origin) => { checked.push(origin); return { ok: true, version: '2.0' }; } });
  const result = await controller.connect({ address: 'starfire.example.com/chat', allowInsecure: false });
  assert.deepEqual(result, { ok: true, origin: 'https://starfire.example.com', version: '2.0' });
  assert.deepEqual(checked, ['https://starfire.example.com']);
  assert.equal(loadSettings(file).settings.address, 'https://starfire.example.com');
  assert.equal(changes[0].next.address, 'https://starfire.example.com');
});

test('connect does not save an address that does not answer as starfire', async () => {
  const { controller, file } = build({ check: async () => ({ ok: false, error: 'nope' }) });
  const result = await controller.connect({ address: 'https://other.example.com', allowInsecure: false });
  assert.deepEqual(result, { ok: false, error: 'nope' });
  assert.equal(fs.existsSync(file), false);
  assert.equal(controller.get().address, '');
});

test('connect refuses an http address without the tick box before any request', async () => {
  let calls = 0;
  const { controller } = build({ check: async () => { calls += 1; return { ok: true, version: '1' }; } });
  const refused = await controller.connect({ address: 'http://192.168.77.5:8080', allowInsecure: false });
  assert.equal(refused.ok, false);
  assert.equal(calls, 0);
  const publicHost = await controller.connect({ address: 'http://example.com', allowInsecure: true });
  assert.equal(publicHost.ok, false);
  assert.equal(calls, 0);
  const accepted = await controller.connect({ address: 'http://192.168.77.5:8080', allowInsecure: true });
  assert.equal(accepted.ok, true);
  assert.equal(controller.get().allowInsecure, true);
  assert.equal(calls, 1);
});

test('connect treats anything but true as an unticked box', async () => {
  const { controller } = build({ check: async () => ({ ok: true, version: '1' }) });
  const result = await controller.connect({ address: 'http://192.168.77.5:8080', allowInsecure: 'true' });
  assert.equal(result.ok, false);
});

test('a new Arty token is saved privately, announced, and cleared on request', () => {
  const token = 'abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE';
  const { controller, file, changes } = build();
  assert.equal(controller.setArtyToken(token).ok, true);
  assert.equal(controller.get().artyToken, token);
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).artyToken, token);
  assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  assert.equal(changes.length, 1);
  assert.equal(controller.setArtyToken('').ok, true);
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).artyToken, '');
  assert.equal(controller.setArtyToken('bad').ok, false);
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).artyToken, '');
});
