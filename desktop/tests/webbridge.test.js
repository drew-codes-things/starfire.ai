const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { WEB_CHANNELS, isBridgeOffered, isStarfirePage, registerWebBridge } = require('../src/webBridge');
const { DEFAULT_SETTINGS } = require('../src/settings');

const ADDRESS = 'https://starfire.example.com';
const TOKEN = 'abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE';
const LINKED = { ...DEFAULT_SETTINGS, address: ADDRESS, artyLink: true, artyToken: TOKEN };

function frame(url, parent = null) {
  return { url, parent };
}

function setup({ settings = LINKED, linkStatus = { state: 'connected', paired: true, computer: true }, look = { ok: true, image: 'x' }, onMainPage = true } = {}) {
  const handlers = new Map();
  const listeners = new Map();
  const ipcMain = { handle: (channel, handler) => handlers.set(channel, handler), on: (channel, handler) => listeners.set(channel, handler) };
  const calls = [];
  const artyLink = {
    status: async () => { calls.push(['status']); return linkStatus; },
    lookAtScreen: async (app) => { calls.push(['look', app]); return look; },
    runComputer: async () => { throw new Error('the web bridge must never run a computer step'); },
    pair: async () => { throw new Error('the web bridge must never pair'); },
    unpair: async () => { throw new Error('the web bridge must never unpair'); },
  };
  registerWebBridge({ ipcMain, controller: { get: () => settings }, artyLink, isMainWindow: () => onMainPage });
  return { handlers, listeners, calls };
}

function asksAllowed(listeners, senderFrame) {
  const event = { senderFrame, returnValue: undefined };
  listeners.get(WEB_CHANNELS.allowed)(event);
  return event.returnValue;
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

test('the bridge has exactly three channels and the link channels of the app\'s own pages are not among them', () => {
  const { handlers, listeners } = setup();
  assert.deepEqual([...handlers.keys(), ...listeners.keys()].sort(), Object.values(WEB_CHANNELS).sort());
  assert.equal(Object.values(WEB_CHANNELS).length, 3);
  assert.ok(Object.values(WEB_CHANNELS).every((channel) => channel.startsWith('starfire-web:')));
});

test('every call from anywhere but the starfire page is refused and does nothing', async () => {
  const { handlers, calls } = setup();
  const strangers = [frame('https://example.org/'), frame(`${ADDRESS}/`, frame(`${ADDRESS}/`)), frame('file:///tmp/pages/settings.html'), null];
  for (const senderFrame of strangers) {
    for (const channel of [WEB_CHANNELS.status, WEB_CHANNELS.look]) {
      await assert.rejects(() => handlers.get(channel)({ senderFrame }, { app: 'Discord' }), /Refused/);
    }
  }
  assert.equal(calls.length, 0);
});

test('calls are refused while the link is off or unpaired, even from the starfire page', async () => {
  for (const settings of [{ ...LINKED, artyLink: false }, { ...LINKED, artyToken: '' }]) {
    const { handlers, calls } = setup({ settings });
    for (const channel of [WEB_CHANNELS.status, WEB_CHANNELS.look]) {
      await assert.rejects(() => handlers.get(channel)({ senderFrame: frame(`${ADDRESS}/`) }, { app: 'Discord' }), /Refused/);
    }
    assert.equal(calls.length, 0);
  }
});

test('the page learns only whether the link is connected, nothing about Arty or the token', async () => {
  for (const [linkStatus, expected] of [
    [{ state: 'connected', paired: true, computer: false }, { connected: true }],
    [{ state: 'unreachable', paired: true }, { connected: false }],
    [{ state: 'unpaired', paired: false }, { connected: false }],
    [{ state: 'off', paired: true }, { connected: false }],
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

test('the web path can only look: it never reaches a computer step, pairing or unpairing', async () => {
  const { handlers } = setup();
  const event = { senderFrame: frame(`${ADDRESS}/`) };
  await handlers.get(WEB_CHANNELS.look)(event, { app: 'Discord' });
  await handlers.get(WEB_CHANNELS.status)(event);
  const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'webBridge.js'), 'utf8');
  assert.doesNotMatch(source, /runComputer|\.pair\(|\.unpair\(|linkComputer|\/link\/computer/);
});

function loadPreload(allowed) {
  const exposed = {};
  const invoked = [];
  const sent = [];
  const electron = {
    contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
    ipcRenderer: {
      sendSync: (channel) => { sent.push(channel); return allowed; },
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
    const { exposed, sent } = loadPreload(answer);
    assert.deepEqual(exposed, {}, String(answer));
    assert.deepEqual(sent, [WEB_CHANNELS.allowed]);
  }
});

test('the preload exposes only a status check and a look, each through its own channel', async () => {
  const { exposed, invoked } = loadPreload(true);
  assert.deepEqual([...Object.keys(exposed)], ['starfireDesktopLink']);
  assert.deepEqual([...Object.keys(exposed.starfireDesktopLink)].sort(), ['linkStatus', 'lookAtScreen']);
  await exposed.starfireDesktopLink.linkStatus();
  await exposed.starfireDesktopLink.lookAtScreen('Discord');
  await exposed.starfireDesktopLink.lookAtScreen();
  assert.deepEqual(invoked, [
    [WEB_CHANNELS.status],
    [WEB_CHANNELS.look, { app: 'Discord' }],
    [WEB_CHANNELS.look, { app: '' }],
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
