const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { NOTICE_CHANNELS, WEB_CHANNELS, VERSION_CHANNEL, registerReplyNotice } = require('../src/webBridge');
const { DEFAULT_SETTINGS } = require('../src/settings');

const ROOT = path.join(__dirname, '..');
const ADDRESS = 'https://starfire.example.com';
const SETTINGS = { ...DEFAULT_SETTINGS, address: ADDRESS };

function frame(url, parent = null) {
  return { url, parent };
}

function noticeFor({ settings = SETTINGS, onMainPage = true } = {}) {
  const listeners = new Map();
  const ipcMain = {
    on: (channel, handler) => listeners.set(channel, handler),
    handle: () => { throw new Error('the reply notice must not add an async channel'); },
  };
  const ready = [];
  registerReplyNotice({ ipcMain, controller: { get: () => settings }, isMainWindow: () => onMainPage,
    onReplyReady: () => ready.push(true) });
  assert.deepEqual([...listeners.keys()].sort(), Object.values(NOTICE_CHANNELS).sort());
  const offered = (senderFrame) => {
    const event = { senderFrame, returnValue: undefined };
    listeners.get(NOTICE_CHANNELS.offered)(event);
    return event.returnValue;
  };
  const tell = (senderFrame) => listeners.get(NOTICE_CHANNELS.ready)({ senderFrame });
  return { offered, tell, ready };
}

function loadPreload(answers) {
  const exposed = {};
  const sent = [];
  const told = [];
  const electron = {
    contextBridge: { exposeInMainWorld: (name, api) => { exposed[name] = api; } },
    ipcRenderer: {
      sendSync: (channel) => { sent.push(channel); return answers[channel]; },
      send: (...args) => { told.push(args); },
      invoke: async () => ({ ok: true }),
      on: () => { throw new Error('the page bridge must not listen for anything'); },
    },
  };
  const source = fs.readFileSync(path.join(ROOT, 'preload-web.js'), 'utf8');
  vm.runInNewContext(`(function (require) { ${source} })`, {})((name) => {
    assert.equal(name, 'electron');
    return electron;
  });
  return { exposed, sent, told };
}

test('the reply notice channels are page channels of their own', () => {
  for (const channel of Object.values(NOTICE_CHANNELS)) {
    assert.ok(channel.startsWith('starfire-web:'));
    assert.ok(!Object.values(WEB_CHANNELS).includes(channel) && channel !== VERSION_CHANNEL);
  }
});

test('the starfire page in the main window may say a reply is ready, and that shows the notice', () => {
  const { offered, tell, ready } = noticeFor();
  assert.equal(offered(frame(`${ADDRESS}/`)), true);
  tell(frame(`${ADDRESS}/chat`));
  assert.deepEqual(ready, [true]);
});

test('any other page, frame or window is not offered the notice and cannot show it', () => {
  const others = [frame('https://example.org/'), frame(`${ADDRESS}/`, frame(`${ADDRESS}/`)),
    frame('file:///tmp/pages/settings.html'), null, undefined];
  const { offered, tell, ready } = noticeFor();
  for (const senderFrame of others) {
    assert.equal(offered(senderFrame), false, JSON.stringify(senderFrame));
    tell(senderFrame);
  }
  const elsewhere = noticeFor({ onMainPage: false });
  assert.equal(elsewhere.offered(frame(`${ADDRESS}/`)), false);
  elsewhere.tell(frame(`${ADDRESS}/`));
  assert.deepEqual([...ready, ...elsewhere.ready], []);
});

test('the preload hands the starfire page one call that only says a reply is ready', () => {
  const { exposed, told } = loadPreload({ [NOTICE_CHANNELS.offered]: true });
  assert.deepEqual(Object.keys(exposed.starfireDesktopReplies), ['ready']);
  exposed.starfireDesktopReplies.ready('anything', { the: 'page' });
  assert.deepEqual(told, [[NOTICE_CHANNELS.ready]]);
});

test('the preload offers no reply call unless the main process says yes', () => {
  for (const answer of [false, undefined, null, 'true', 1, {}]) {
    const { exposed } = loadPreload({ [NOTICE_CHANNELS.offered]: answer });
    assert.equal('starfireDesktopReplies' in exposed, false, String(answer));
  }
});

test('the main process shows the notice when the page says so, never when a chat stream merely ends', () => {
  const main = fs.readFileSync(path.join(ROOT, 'main.js'), 'utf8');
  assert.match(main, /registerReplyNotice\(\{ ipcMain, controller, isMainWindow: \(event\) => windows\.isMainContents\(event\.sender\), onReplyReady: showReplyNotice \}\)/);
  assert.doesNotMatch(main, /onCompleted|chat_stream/);
});
