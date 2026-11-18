const test = require('node:test');
const assert = require('node:assert/strict');
const { parseAccelerator, acceleratorFromKeyEvent, registerQuickAsk, DEFAULT_SHORTCUT } = require('../src/shortcut');

test('the default shortcut is Ctrl+Shift+Space and is valid', () => {
  assert.equal(DEFAULT_SHORTCUT, 'Ctrl+Shift+Space');
  assert.deepEqual(parseAccelerator(DEFAULT_SHORTCUT), { ok: true, accelerator: 'Ctrl+Shift+Space' });
});

test('accelerators are normalised to one spelling and order', () => {
  const cases = {
    'ctrl+shift+space': 'Ctrl+Shift+Space',
    'Shift+Control+K': 'Ctrl+Shift+K',
    'alt+ctrl+f5': 'Ctrl+Alt+F5',
    ' CommandOrControl + Alt + 7 ': 'CommandOrControl+Alt+7',
    'Super+Q': 'Super+Q',
    'meta+q': 'Super+Q',
    'Ctrl+Alt+Shift+Space': 'Ctrl+Alt+Shift+Space',
  };
  for (const [input, expected] of Object.entries(cases)) {
    assert.deepEqual(parseAccelerator(input), { ok: true, accelerator: expected }, input);
  }
});

test('invalid accelerators are refused with a reason', () => {
  const bad = ['', 'Space', 'A', 'Shift+A', 'Ctrl+Shift', 'Ctrl+A+B', 'Ctrl+Banana', 'Ctrl+Ctrl+A', 'Ctrl+', '+A', 'Ctrl+Shift+F25', 'Ctrl+Escape', undefined, null, 5];
  for (const input of bad) {
    const result = parseAccelerator(input);
    assert.equal(result.ok, false, String(input));
    assert.ok(result.error.length > 0);
  }
});

test('a key press becomes an accelerator by its physical key', () => {
  assert.deepEqual(acceleratorFromKeyEvent({ code: 'KeyK', key: 'k', ctrlKey: true, shiftKey: true }), { ok: true, accelerator: 'Ctrl+Shift+K' });
  assert.deepEqual(acceleratorFromKeyEvent({ code: 'Space', key: ' ', ctrlKey: true, altKey: true }), { ok: true, accelerator: 'Ctrl+Alt+Space' });
  assert.deepEqual(acceleratorFromKeyEvent({ code: 'Digit5', key: '%', ctrlKey: true, shiftKey: true }), { ok: true, accelerator: 'Ctrl+Shift+5' });
  assert.deepEqual(acceleratorFromKeyEvent({ code: 'F9', key: 'F9', altKey: true }), { ok: true, accelerator: 'Alt+F9' });
  assert.deepEqual(acceleratorFromKeyEvent({ code: 'KeyQ', key: 'q', metaKey: true }), { ok: true, accelerator: 'Super+Q' });
});

test('a modifier alone is incomplete and a key without a modifier is refused', () => {
  const alone = acceleratorFromKeyEvent({ code: 'ShiftLeft', key: 'Shift', shiftKey: true });
  assert.equal(alone.ok, false);
  assert.equal(alone.incomplete, true);
  const bare = acceleratorFromKeyEvent({ code: 'KeyK', key: 'k' });
  assert.equal(bare.ok, false);
  assert.match(bare.error, /Ctrl|Alt|modifier/i);
  const odd = acceleratorFromKeyEvent({ code: 'MediaPlayPause', key: 'MediaPlayPause', ctrlKey: true });
  assert.equal(odd.ok, false);
});

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

test('registering the shortcut makes it call the handler', () => {
  const globalShortcut = fakeGlobalShortcut();
  let calls = 0;
  const result = registerQuickAsk(globalShortcut, { next: 'Ctrl+Shift+Space', previous: null, handler: () => { calls += 1; } });
  assert.deepEqual(result, { ok: true, accelerator: 'Ctrl+Shift+Space' });
  globalShortcut.registered.get('Ctrl+Shift+Space')();
  assert.equal(calls, 1);
});

test('changing the shortcut releases the old one', () => {
  const globalShortcut = fakeGlobalShortcut();
  registerQuickAsk(globalShortcut, { next: 'Ctrl+Shift+Space', previous: null, handler: () => {} });
  const result = registerQuickAsk(globalShortcut, { next: 'Ctrl+Alt+K', previous: 'Ctrl+Shift+Space', handler: () => {} });
  assert.equal(result.ok, true);
  assert.deepEqual([...globalShortcut.registered.keys()], ['Ctrl+Alt+K']);
});

test('a shortcut that is taken keeps the old one working and says so', () => {
  const globalShortcut = fakeGlobalShortcut({ taken: ['Ctrl+Alt+K'] });
  registerQuickAsk(globalShortcut, { next: 'Ctrl+Shift+Space', previous: null, handler: () => {} });
  const result = registerQuickAsk(globalShortcut, { next: 'Ctrl+Alt+K', previous: 'Ctrl+Shift+Space', handler: () => {} });
  assert.equal(result.ok, false);
  assert.match(result.error, /Ctrl\+Alt\+K/);
  assert.match(result.error, /in use|taken|already/i);
  assert.deepEqual([...globalShortcut.registered.keys()], ['Ctrl+Shift+Space']);
});

test('a first registration that fails reports it and leaves nothing registered', () => {
  const globalShortcut = fakeGlobalShortcut({ taken: ['Ctrl+Shift+Space'] });
  const result = registerQuickAsk(globalShortcut, { next: 'Ctrl+Shift+Space', previous: null, handler: () => {} });
  assert.equal(result.ok, false);
  assert.equal(globalShortcut.registered.size, 0);
});

test('a registration that throws is reported, not raised', () => {
  const globalShortcut = fakeGlobalShortcut();
  globalShortcut.register = () => { throw new TypeError('bad accelerator'); };
  const result = registerQuickAsk(globalShortcut, { next: 'Ctrl+Shift+Space', previous: null, handler: () => {} });
  assert.equal(result.ok, false);
  assert.match(result.error, /bad accelerator/);
});

test('registering the same shortcut again is fine', () => {
  const globalShortcut = fakeGlobalShortcut();
  registerQuickAsk(globalShortcut, { next: 'Ctrl+Shift+Space', previous: null, handler: () => {} });
  const result = registerQuickAsk(globalShortcut, { next: 'Ctrl+Shift+Space', previous: 'Ctrl+Shift+Space', handler: () => {} });
  assert.equal(result.ok, true);
  assert.equal(globalShortcut.registered.size, 1);
});
