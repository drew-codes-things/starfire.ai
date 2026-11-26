const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { WEB_CHANNELS, VERSION_CHANNEL, registerVersionAnswer, isPageBridgeChanged } = require('../src/webBridge');
const { DEFAULT_SETTINGS, applySettingsPatch, loadSettings } = require('../src/settings');
const os = require('node:os');

const ROOT = path.join(__dirname, '..');
const ADDRESS = 'https://starfire.example.com';
const UNLINKED = { ...DEFAULT_SETTINGS, address: ADDRESS, artyLink: false, artyToken: '' };

function frame(url, parent = null) {
  return { url, parent };
}

function answerFor({ settings = UNLINKED, onMainPage = true, version = '1.1.0' } = {}) {
  const listeners = new Map();
  const ipcMain = {
    on: (channel, handler) => listeners.set(channel, handler),
    handle: () => { throw new Error('the version answer must not add an async channel'); },
  };
  registerVersionAnswer({ ipcMain, controller: { get: () => settings }, isMainWindow: () => onMainPage, version });
  assert.deepEqual([...listeners.keys()], [VERSION_CHANNEL]);
  return (senderFrame) => {
    const event = { senderFrame, returnValue: undefined };
    listeners.get(VERSION_CHANNEL)(event);
    return event.returnValue;
  };
}

function loadPreload(answers) {
  const exposed = {};
  const sent = [];
  const electron = {
    contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
    ipcRenderer: {
      sendSync: (channel) => { sent.push(channel); return answers[channel]; },
      invoke: async () => ({ ok: true }),
      on: () => { throw new Error('the page bridge must not listen for anything'); },
    },
  };
  const source = fs.readFileSync(path.join(ROOT, 'preload-web.js'), 'utf8');
  vm.runInNewContext(`(function (require) { ${source} })`, {})((name) => {
    assert.equal(name, 'electron');
    return electron;
  });
  return { exposed, sent };
}

test('the version channel is a page channel of its own, outside the Arty link channels', () => {
  assert.equal(VERSION_CHANNEL, 'starfire-web:version');
  assert.ok(!Object.values(WEB_CHANNELS).includes(VERSION_CHANNEL));
});

test('the starfire page in the main window learns the Desktop version with the Arty link off', () => {
  const ask = answerFor();
  assert.equal(ask(frame(`${ADDRESS}/`)), '1.1.0');
  assert.equal(ask(frame(`${ADDRESS}/chat?x=1`)), '1.1.0');
});

test('any other page, frame or window learns nothing', () => {
  const ask = answerFor();
  for (const senderFrame of [frame('https://example.org/'), frame(`${ADDRESS}/`, frame(`${ADDRESS}/`)),
    frame('file:///tmp/pages/settings.html'), frame('http://starfire.example.com/'), null, undefined]) {
    assert.equal(ask(senderFrame), '', JSON.stringify(senderFrame));
  }
  assert.equal(answerFor({ onMainPage: false })(frame(`${ADDRESS}/`)), '');
  assert.equal(answerFor({ settings: { ...UNLINKED, address: '' } })(frame(`${ADDRESS}/`)), '');
});

test('the preload hands the page the version alone, whether or not the Arty link is offered', () => {
  for (const allowed of [false, true]) {
    const { exposed, sent } = loadPreload({ [WEB_CHANNELS.allowed]: allowed, [VERSION_CHANNEL]: '1.1.0' });
    assert.deepEqual(Object.keys(exposed.starfireDesktopApp), ['version']);
    assert.equal(exposed.starfireDesktopApp.version, '1.1.0');
    assert.deepEqual(Object.keys(exposed).sort(), allowed ? ['starfireDesktopApp', 'starfireDesktopLink'] : ['starfireDesktopApp']);
    assert.deepEqual(sent, allowed ? [VERSION_CHANNEL, WEB_CHANNELS.allowed] : [VERSION_CHANNEL, WEB_CHANNELS.allowed, WEB_CHANNELS.unlinked]);
  }
});

test('the preload hands the page nothing when the main process gives no version', () => {
  for (const answer of ['', undefined, null, 1, true, {}, ['1.1.0'], 'true', '1.1.0<b>', ' 1.1.0', '1.1.0.0.0', '1234567.0']) {
    const { exposed } = loadPreload({ [WEB_CHANNELS.allowed]: false, [VERSION_CHANNEL]: answer });
    assert.deepEqual(exposed, {}, String(answer));
  }
});

test('the main process answers with its own app version', () => {
  const main = fs.readFileSync(path.join(ROOT, 'main.js'), 'utf8');
  assert.match(main, /registerVersionAnswer\(\{ ipcMain, controller, isMainWindow: \(event\) => windows\.isMainContents\(event\.sender\), version: app\.getVersion\(\) \}\)/);
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
  assert.equal(pkg.version, '1.1.0');
});

test('the main process still makes no outside request for the version', () => {
  for (const file of ['main.js', 'preload-web.js', path.join('src', 'webBridge.js')]) {
    const source = fs.readFileSync(path.join(ROOT, file), 'utf8');
    assert.doesNotMatch(source, /api\.github\.com|net\.request|https\.get|fetch\(/, file);
  }
});

test('the update notice switch is on by default and saved like the other switches', () => {
  assert.equal(DEFAULT_SETTINGS.updateNotice, true);
  const off = applySettingsPatch(UNLINKED, { updateNotice: false });
  assert.equal(off.ok, true);
  assert.equal(off.settings.updateNotice, false);
  assert.equal(applySettingsPatch(UNLINKED, { updateNotice: 'no' }).ok, false);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'starfire-notice-'));
  const file = path.join(dir, 'config.json');
  for (const [stored, expected] of [[false, false], [true, true], ['off', true], [0, true]]) {
    fs.writeFileSync(file, JSON.stringify({ updateNotice: stored }));
    assert.equal(loadSettings(file).settings.updateNotice, expected, JSON.stringify(stored));
  }
  fs.rmSync(dir, { recursive: true, force: true });
});

test('with the switch off the page is told no version, so it never checks', () => {
  const ask = answerFor({ settings: { ...UNLINKED, updateNotice: false } });
  assert.equal(ask(frame(`${ADDRESS}/`)), '');
});

test('flipping the switch reloads the starfire window so the page follows it', () => {
  assert.equal(isPageBridgeChanged(UNLINKED, { ...UNLINKED, updateNotice: false }), true);
  assert.equal(isPageBridgeChanged({ ...UNLINKED, updateNotice: false }, UNLINKED), true);
  assert.equal(isPageBridgeChanged(UNLINKED, { ...UNLINKED, notifications: false }), false);
  const linked = { ...UNLINKED, artyLink: true, artyToken: 'abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE' };
  assert.equal(isPageBridgeChanged(linked, UNLINKED), true);
  const main = fs.readFileSync(path.join(ROOT, 'main.js'), 'utf8');
  assert.match(main, /if \(windows && isPageBridgeChanged\(current, previous\)\) windows\.reloadMain\(\);/);
});

test('the settings page has the switch with a plain label', () => {
  const html = fs.readFileSync(path.join(ROOT, 'pages', 'settings.html'), 'utf8');
  const script = fs.readFileSync(path.join(ROOT, 'pages', 'settings.js'), 'utf8');
  assert.match(html, /<strong>Tell me when a new starfire Desktop is out<\/strong>/);
  assert.match(html, /<input id="updateNotice" type="checkbox" aria-label="Tell me when a new starfire Desktop is out">/);
  assert.match(script, /const TOGGLES = \[[^\]]*'updateNotice'[^\]]*\];/);
});
