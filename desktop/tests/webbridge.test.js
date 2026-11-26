const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { WEB_CHANNELS, VERSION_CHANNEL, isBridgeOffered, isStarfirePage, registerWebBridge } = require('../src/webBridge');
const { DEFAULT_SETTINGS } = require('../src/settings');

const ADDRESS = 'https://starfire.example.com';
const TOKEN = 'abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE';
const LINKED = { ...DEFAULT_SETTINGS, address: ADDRESS, artyLink: true, artyToken: TOKEN };

function frame(url, parent = null) {
  return { url, parent };
}

function setup({ settings = LINKED, linkStatus = { state: 'connected', paired: true, computer: true, capabilities: [] }, look = { ok: true, image: 'x' }, opened = { ok: true, browser: 'Firefox', tab: 3 }, stepped = { ok: true, result: 'done' }, onMainPage = true } = {}) {
  const handlers = new Map();
  const listeners = new Map();
  const ipcMain = { handle: (channel, handler) => handlers.set(channel, handler), on: (channel, handler) => listeners.set(channel, handler) };
  const calls = [];
  const artyLink = {
    status: async () => { calls.push(['status']); return linkStatus; },
    lookAtScreen: async (app) => { calls.push(['look', app]); return look; },
    openInBrowser: async (url, browser) => { calls.push(['open', url, browser]); return opened; },
    controlStep: async (action, args) => { calls.push(['step', action, args]); return stepped; },
    runComputer: async () => { throw new Error('the web bridge must never run a computer step'); },
    pair: async () => { throw new Error('the web bridge must never pair'); },
    unpair: async () => { throw new Error('the web bridge must never unpair'); },
  };
  registerWebBridge({ ipcMain, controller: { get: () => settings }, artyLink, isMainWindow: () => onMainPage });
  return { handlers, listeners, calls };
}

function asksAllowed(listeners, senderFrame, channel = WEB_CHANNELS.allowed) {
  const event = { senderFrame, returnValue: undefined };
  listeners.get(channel)(event);
  return event.returnValue;
}

function asksUnlinked(listeners, senderFrame) {
  return asksAllowed(listeners, senderFrame, WEB_CHANNELS.unlinked);
}

test('it is offered only when the link is on and paired', () => {
  assert.equal(isBridgeOffered(LINKED), true);
  assert.equal(isBridgeOffered({ ...LINKED, artyLink: false }), false);
  assert.equal(isBridgeOffered({ ...LINKED, artyToken: '' }), false);
  assert.equal(isBridgeOffered(DEFAULT_SETTINGS), false);
});

test('only the top frame of the configured starfire origin is the starfire page', () => {
  assert.equal(isStarfirePage(frame(`${ADDRESS}/`), ADDRESS), true);
  assert.equal(isStarfirePage(frame(`${ADDRESS}/chat?x=1`), ADDRESS), true);
  const refused = [
    frame(`${ADDRESS}/`, frame(`${ADDRESS}/`)),
    frame('https://starfire.example.com.evil.com/'),
    frame('http://starfire.example.com/'),
    frame('https://starfire.example.com:8443/'),
    frame('file:///tmp/pages/settings.html'),
    frame('about:blank'),
    frame(''),
    null,
    undefined,
  ];
  for (const candidate of refused) assert.equal(isStarfirePage(candidate, ADDRESS), false, JSON.stringify(candidate));
  assert.equal(isStarfirePage(frame(`${ADDRESS}/`), ''), false);
});

test('the preload is told yes only for the starfire page in the main window with the link on and paired', () => {
  const yes = setup();
  assert.equal(asksAllowed(yes.listeners, frame(`${ADDRESS}/`)), true);
  assert.equal(asksAllowed(yes.listeners, frame('https://example.org/')), false);
  assert.equal(asksAllowed(yes.listeners, frame(`${ADDRESS}/`, frame(`${ADDRESS}/`))), false);
  assert.equal(asksAllowed(setup({ onMainPage: false }).listeners, frame(`${ADDRESS}/`)), false);
  assert.equal(asksAllowed(setup({ settings: { ...LINKED, artyLink: false } }).listeners, frame(`${ADDRESS}/`)), false);
  assert.equal(asksAllowed(setup({ settings: { ...LINKED, artyToken: '' } }).listeners, frame(`${ADDRESS}/`)), false);
});

test('the preload is told the link is off only for the starfire page in the main window with the link off or unpaired', () => {
  const page = frame(`${ADDRESS}/`);
  for (const settings of [{ ...LINKED, artyLink: false }, { ...LINKED, artyToken: '' }, { ...DEFAULT_SETTINGS, address: ADDRESS }]) {
    const { listeners } = setup({ settings });
    assert.equal(asksUnlinked(listeners, page), true, JSON.stringify(settings));
    assert.equal(asksUnlinked(listeners, frame('https://example.org/')), false);
    assert.equal(asksUnlinked(listeners, frame(`${ADDRESS}/`, page)), false);
    assert.equal(asksUnlinked(setup({ settings, onMainPage: false }).listeners, page), false);
  }
  assert.equal(asksUnlinked(setup().listeners, page), false);
  const { calls } = setup({ settings: { ...LINKED, artyLink: false } });
  assert.deepEqual(calls, []);
});

test('the bridge has exactly six channels and the link channels of the app\'s own pages are not among them', () => {
  const { handlers, listeners } = setup();
  assert.deepEqual([...handlers.keys(), ...listeners.keys()].sort(), Object.values(WEB_CHANNELS).sort());
  assert.equal(Object.values(WEB_CHANNELS).length, 6);
  assert.ok(Object.values(WEB_CHANNELS).every((channel) => channel.startsWith('starfire-web:')));
});

test('every call from anywhere but the starfire page is refused and does nothing', async () => {
  const { handlers, calls } = setup();
  const strangers = [frame('https://example.org/'), frame(`${ADDRESS}/`, frame(`${ADDRESS}/`)), frame('file:///tmp/pages/settings.html'), null];
  for (const senderFrame of strangers) {
    for (const channel of [WEB_CHANNELS.status, WEB_CHANNELS.look, WEB_CHANNELS.open, WEB_CHANNELS.control]) {
      await assert.rejects(() => handlers.get(channel)({ senderFrame }, { app: 'Discord' }), /Refused/);
    }
    await assert.rejects(() => handlers.get(WEB_CHANNELS.open)({ senderFrame }, { url: 'https://example.org/', browser: '' }), /Refused/);
    await assert.rejects(() => handlers.get(WEB_CHANNELS.control)({ senderFrame }, { action: 'apps', arguments: {} }), /Refused/);
  }
  assert.equal(calls.length, 0);
});

test('an open or a step from the starfire page in another window is refused and does nothing', async () => {
  const { handlers, calls } = setup({ onMainPage: false });
  await assert.rejects(() => handlers.get(WEB_CHANNELS.open)({ senderFrame: frame(`${ADDRESS}/`) }, { url: 'https://example.org/', browser: '' }), /Refused/);
  await assert.rejects(() => handlers.get(WEB_CHANNELS.control)({ senderFrame: frame(`${ADDRESS}/`) }, { action: 'apps', arguments: {} }), /Refused/);
  assert.equal(calls.length, 0);
});

test('calls are refused while the link is off or unpaired, even from the starfire page', async () => {
  for (const settings of [{ ...LINKED, artyLink: false }, { ...LINKED, artyToken: '' }]) {
    const { handlers, calls } = setup({ settings });
    for (const channel of [WEB_CHANNELS.status, WEB_CHANNELS.look, WEB_CHANNELS.open, WEB_CHANNELS.control]) {
      await assert.rejects(() => handlers.get(channel)({ senderFrame: frame(`${ADDRESS}/`) }, { app: 'Discord' }), /Refused/);
    }
    assert.equal(calls.length, 0);
  }
});

test('the page learns only whether the link is connected and what it can do, nothing about Arty or the token', async () => {
  const capabilities = [{ id: 'browser.open', version: 1, available: true, browsers: [{ name: 'Firefox', state: 'connected' }] }];
  for (const [linkStatus, expected] of [
    [{ state: 'connected', paired: true, computer: false, artyDesktop: '1.6.0', capabilities }, { connected: true, capabilities }],
    [{ state: 'connected', paired: true, computer: false }, { connected: true, capabilities: [] }],
    [{ state: 'unreachable', paired: true, capabilities }, { connected: false, capabilities: [] }],
    [{ state: 'unpaired', paired: false, capabilities: [] }, { connected: false, capabilities: [] }],
    [{ state: 'off', paired: true, capabilities: [] }, { connected: false, capabilities: [] }],
  ]) {
    const { handlers } = setup({ linkStatus });
    assert.deepEqual(await handlers.get(WEB_CHANNELS.status)({ senderFrame: frame(`${ADDRESS}/`) }), expected);
  }
});

test('a look passes only the app name on, and nothing else can be sent', async () => {
  const { handlers, calls } = setup();
  const event = { senderFrame: frame(`${ADDRESS}/`) };
  assert.deepEqual(await handlers.get(WEB_CHANNELS.look)(event, { app: 'Discord' }), { ok: true, image: 'x' });
  assert.deepEqual(calls, [['look', 'Discord']]);
  for (const request of [{ app: 'Discord', tool: 'computer_run_command' }, { app: 'Discord', arguments: {} }, { command: 'id' }, { app: 5 }, {}, null, 'Discord', ['Discord'], undefined]) {
    const result = await handlers.get(WEB_CHANNELS.look)(event, request);
    assert.equal(result.ok, false, JSON.stringify(request));
    assert.equal(result.reason, 'bad_request');
  }
  assert.equal(calls.length, 1);
});

test('an open passes only the address and the browser on, and nothing else can be sent', async () => {
  const { handlers, calls } = setup();
  const event = { senderFrame: frame(`${ADDRESS}/`) };
  assert.deepEqual(await handlers.get(WEB_CHANNELS.open)(event, { url: 'https://example.org/', browser: 'Firefox' }), { ok: true, browser: 'Firefox', tab: 3 });
  assert.deepEqual(await handlers.get(WEB_CHANNELS.open)(event, { browser: '', url: 'https://example.org/a' }), { ok: true, browser: 'Firefox', tab: 3 });
  assert.deepEqual(calls, [['open', 'https://example.org/', 'Firefox'], ['open', 'https://example.org/a', '']]);
  const wrong = [
    { url: 'https://example.org/' }, { browser: 'Firefox' }, { url: 'https://example.org/', browser: 'Firefox', action: 'close' },
    { url: 'https://example.org/', browser: 5 }, { url: ['https://example.org/'], browser: '' }, { url: null, browser: '' },
    { app: 'Discord' }, {}, null, 'https://example.org/', ['https://example.org/', ''], undefined,
  ];
  for (const request of wrong) {
    const result = await handlers.get(WEB_CHANNELS.open)(event, request);
    assert.equal(result.ok, false, JSON.stringify(request));
    assert.equal(result.reason, 'bad_request');
  }
  assert.equal(calls.length, 2);
});

test('a step passes only an action and its values on, and nothing else can be sent', async () => {
  const { handlers, calls } = setup();
  const event = { senderFrame: frame(`${ADDRESS}/`) };
  assert.deepEqual(await handlers.get(WEB_CHANNELS.control)(event, { action: 'key', arguments: { keys: 'ctrl+k' } }), { ok: true, result: 'done' });
  assert.deepEqual(await handlers.get(WEB_CHANNELS.control)(event, { arguments: {}, action: 'apps' }), { ok: true, result: 'done' });
  assert.deepEqual(calls, [['step', 'key', { keys: 'ctrl+k' }], ['step', 'apps', {}]]);
  const wrong = [
    { action: 'apps' }, { arguments: {} }, { action: 'apps', arguments: {}, tool: 'computer_run_command' },
    { action: 5, arguments: {} }, { action: 'apps', arguments: null }, { action: 'apps', arguments: [] }, { action: 'apps', arguments: 'x' },
    { tool: 'computer_run_command', arguments: { command: 'id' } }, {}, null, 'apps', ['apps', {}], undefined,
  ];
  for (const request of wrong) {
    const result = await handlers.get(WEB_CHANNELS.control)(event, request);
    assert.equal(result.ok, false, JSON.stringify(request));
    assert.equal(result.reason, 'bad_request');
  }
  assert.equal(calls.length, 2);
});

test('the web path never names a tool, the computer route, pairing or unpairing: steps go through the link\'s own fixed list', async () => {
  const { handlers } = setup();
  const event = { senderFrame: frame(`${ADDRESS}/`) };
  await handlers.get(WEB_CHANNELS.look)(event, { app: 'Discord' });
  await handlers.get(WEB_CHANNELS.status)(event);
  const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'webBridge.js'), 'utf8');
  assert.doesNotMatch(source, /runComputer|\.pair\(|\.unpair\(|linkComputer|\/link\/computer|computer_|tryLink/);
});

function loadPreload(allowed, unlinked) {
  const exposed = {};
  const invoked = [];
  const sent = [];
  const electron = {
    contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
    ipcRenderer: {
      sendSync: (channel) => { sent.push(channel); return channel === WEB_CHANNELS.unlinked ? unlinked : allowed; },
      invoke: async (...args) => { invoked.push(JSON.parse(JSON.stringify(args))); return { ok: true }; },
      on: () => { throw new Error('the page bridge must not listen for anything'); },
    },
  };
  const source = fs.readFileSync(path.join(__dirname, '..', 'preload-web.js'), 'utf8');
  vm.runInNewContext(`(function (require) { ${source} })`, {})((name) => {
    assert.equal(name, 'electron');
    return electron;
  });
  return { exposed, invoked, sent };
}

test('the preload exposes nothing unless the main process says yes', () => {
  for (const answer of [false, undefined, null, 'true', 1, {}]) {
    const { exposed, sent } = loadPreload(answer, answer);
    assert.deepEqual(exposed, {}, String(answer));
    assert.deepEqual(sent, [VERSION_CHANNEL, WEB_CHANNELS.allowed, WEB_CHANNELS.unlinked]);
  }
});

test('with the link off the preload tells the starfire page only that, and offers it nothing to call', () => {
  const { exposed, invoked, sent } = loadPreload(false, true);
  assert.deepEqual(exposed, { starfireDesktopUnlinked: true });
  assert.deepEqual(sent, [VERSION_CHANNEL, WEB_CHANNELS.allowed, WEB_CHANNELS.unlinked]);
  assert.deepEqual(invoked, []);
});

test('with the link on the preload never asks whether it is off', () => {
  const { exposed, sent } = loadPreload(true, true);
  assert.deepEqual(sent, [VERSION_CHANNEL, WEB_CHANNELS.allowed]);
  assert.equal('starfireDesktopUnlinked' in exposed, false);
});

test('the preload exposes only a status check, a look, an open and a step, each through its own channel', async () => {
  const { exposed, invoked } = loadPreload(true);
  assert.deepEqual([...Object.keys(exposed)], ['starfireDesktopLink']);
  assert.deepEqual([...Object.keys(exposed.starfireDesktopLink)].sort(), ['controlStep', 'linkStatus', 'lookAtScreen', 'openInBrowser']);
  await exposed.starfireDesktopLink.linkStatus();
  await exposed.starfireDesktopLink.lookAtScreen('Discord');
  await exposed.starfireDesktopLink.lookAtScreen();
  await exposed.starfireDesktopLink.openInBrowser('https://example.org/', 'Firefox');
  await exposed.starfireDesktopLink.openInBrowser('https://example.org/');
  assert.deepEqual(invoked, [
    [WEB_CHANNELS.status],
    [WEB_CHANNELS.look, { app: 'Discord' }],
    [WEB_CHANNELS.look, { app: '' }],
    [WEB_CHANNELS.open, { url: 'https://example.org/', browser: 'Firefox' }],
    [WEB_CHANNELS.open, { url: 'https://example.org/', browser: '' }],
  ]);
});

test('the preload sends only text as the address and browser, whatever the page hands it', async () => {
  const { exposed, invoked } = loadPreload(true);
  await exposed.starfireDesktopLink.openInBrowser({ toString: () => 'https://example.org/' }, ['Firefox']);
  await exposed.starfireDesktopLink.openInBrowser(5, { name: 'Firefox' });
  assert.deepEqual(invoked.map((call) => call[1]), [{ url: '', browser: '' }, { url: '', browser: '' }]);
});

test('the preload sends a step as text and plain values only, whatever the page hands it', async () => {
  const { exposed, invoked } = loadPreload(true);
  await exposed.starfireDesktopLink.controlStep('click', { target: 'Send', x: 3, double: true, nested: { a: 1 }, list: [1], gone: undefined, run: () => 1 });
  await exposed.starfireDesktopLink.controlStep({ toString: () => 'apps' }, ['x']);
  await exposed.starfireDesktopLink.controlStep('apps');
  assert.deepEqual(invoked.map((call) => call[0]), [WEB_CHANNELS.control, WEB_CHANNELS.control, WEB_CHANNELS.control]);
  assert.deepEqual(invoked.map((call) => call[1]), [
    { action: 'click', arguments: { target: 'Send', x: 3, double: true } },
    { action: '', arguments: {} },
    { action: 'apps', arguments: {} },
  ]);
});

test('the preload sends only text as the app, whatever the page hands it', async () => {
  const { exposed, invoked } = loadPreload(true);
  await exposed.starfireDesktopLink.lookAtScreen({ toString: () => 'x', tool: 'computer_run_command' });
  await exposed.starfireDesktopLink.lookAtScreen(['Discord']);
  assert.deepEqual(invoked.map((call) => call[1]), [{ app: '' }, { app: '' }]);
});

test('the web preload uses no channel the main process does not answer', () => {
  const preload = fs.readFileSync(path.join(__dirname, '..', 'preload-web.js'), 'utf8');
  assert.doesNotMatch(preload, /starfire:/);
  assert.doesNotMatch(preload, /link-computer|linkComputer|linkPair|linkUnpair/);
});
