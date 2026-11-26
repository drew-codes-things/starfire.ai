'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.join(__dirname, '..', '..', 'static');
const {
  DESKTOP_UPDATE_FIRST_CHECK_MS, DESKTOP_UPDATE_EVERY_MS, DESKTOP_UPDATE_DISMISSED_KEY,
  isNewerDesktopVersion, desktopAppVersion, desktopUpdateToShow, createDesktopUpdateNotice, startDesktopUpdateCheck,
} = require(path.join(STATIC, 'desktopUpdateLogic.js'));

const RELEASE_PAGE = 'https://github.com/drew-codes-things/starfire.ai/releases/tag/';
const NEWER = { check: true, version: '1.2.0', url: `${RELEASE_PAGE}desktop-v1.2.0` };

class FakeElement {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.textContent = '';
    this.className = '';
    this.parent = null;
  }

  append(...children) {
    for (const child of children) {
      child.parent = this;
      this.children.push(child);
    }
  }

  appendChild(child) { this.append(child); return child; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(kind, listener) { this.listeners[kind] = listener; }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((child) => child !== this);
    this.parent = null;
  }

  set innerHTML(value) { throw new Error(`innerHTML was set to ${value}`); }
  find(tag) { return this.children.find((child) => child.tag === tag); }
}

function documentFake() {
  const box = new FakeElement('div');
  return { box, createElement: (tag) => new FakeElement(tag), getElementById: (id) => (id === 'toasts' ? box : null) };
}

function storageFake(initial = {}) {
  const values = { ...initial };
  return { values, getItem: (key) => (key in values ? values[key] : null), setItem: (key, value) => { values[key] = String(value); } };
}

function timersFake() {
  const later = [];
  const repeats = [];
  return {
    later,
    repeats,
    setTimeout: (callback, ms) => later.push([callback, ms]),
    setInterval: (callback, ms) => repeats.push([callback, ms]),
  };
}

function desktopWindow(version = '1.1.0') {
  return { starfireDesktopApp: { version } };
}

async function started({ win = desktopWindow(), answer = NEWER, storage = storageFake(), fail = null } = {}) {
  const doc = documentFake();
  const timers = timersFake();
  const asked = [];
  const logged = [];
  const isStarted = startDesktopUpdateCheck({
    win,
    document: doc,
    storage,
    timers,
    log: (line) => logged.push(line),
    fetchLatest: async () => {
      asked.push(1);
      if (fail) throw fail;
      return answer;
    },
  });
  return { isStarted, doc, timers, asked, logged, storage };
}

async function runFirstCheck(state) {
  await state.timers.later[0][0]();
  return state;
}

function noticeIn(doc) {
  return doc.box.children.find((child) => child.className.includes('desktop-update'));
}

test('versions compare as numbers and tolerate a v or a desktop-v prefix', () => {
  assert.equal(isNewerDesktopVersion('1.2.0', '1.1.0'), true);
  assert.equal(isNewerDesktopVersion('1.10.0', '1.9.0'), true);
  assert.equal(isNewerDesktopVersion('desktop-v1.1.1', '1.1.0'), true);
  assert.equal(isNewerDesktopVersion('v2', '1.9.9'), true);
  assert.equal(isNewerDesktopVersion('1.1', '1.1.0'), false);
  assert.equal(isNewerDesktopVersion('1.1.0', '1.1.0'), false);
  assert.equal(isNewerDesktopVersion('1.0.9', '1.1.0'), false);
  for (const odd of ['', 'latest', '1.x', null, undefined, 5, '1.2.0-beta', '1.2.0<b>']) {
    assert.equal(isNewerDesktopVersion(odd, '1.1.0'), false, String(odd));
    assert.equal(isNewerDesktopVersion('9.0.0', odd), false, String(odd));
  }
});

test('the Desktop version comes only from the preload and only in its expected shape', () => {
  assert.equal(desktopAppVersion(desktopWindow('1.1.0')), '1.1.0');
  for (const win of [{}, null, undefined, { starfireDesktopApp: null }, { starfireDesktopApp: {} }, desktopWindow(5), desktopWindow('soon'), desktopWindow('')]) {
    assert.equal(desktopAppVersion(win), '', JSON.stringify(win));
  }
});

test('a notice is due only for a newer release on the starfire.ai release page that was not dismissed', () => {
  assert.deepEqual(desktopUpdateToShow(NEWER, '1.1.0', ''), { version: '1.2.0', url: NEWER.url });
  assert.equal(desktopUpdateToShow(NEWER, '1.2.0', ''), null);
  assert.equal(desktopUpdateToShow(NEWER, '1.1.0', '1.2.0'), null);
  assert.deepEqual(desktopUpdateToShow(NEWER, '1.1.0', '1.1.5'), { version: '1.2.0', url: NEWER.url });
  for (const answer of [null, undefined, 'x', {}, { ...NEWER, check: false }, { ...NEWER, version: null }, { ...NEWER, url: null },
    { ...NEWER, url: 'https://example.org/desktop-v1.2.0' }, { ...NEWER, url: 'javascript:alert(1)' },
    { ...NEWER, url: `${RELEASE_PAGE}desktop-v1.2.0"onclick` }, { ...NEWER, url: 'https://github.com/someone/else/releases/tag/desktop-v1.2.0' }]) {
    assert.equal(desktopUpdateToShow(answer, '1.1.0', ''), null, JSON.stringify(answer));
  }
});

test('outside starfire Desktop nothing is scheduled and the server is never asked', async () => {
  for (const win of [{}, { starfireDesktopApp: {} }, desktopWindow('nope')]) {
    const state = await started({ win });
    assert.equal(state.isStarted, false);
    assert.deepEqual(state.timers.later, []);
    assert.deepEqual(state.timers.repeats, []);
    assert.deepEqual(state.asked, []);
  }
});

test('inside starfire Desktop it checks shortly after start, then once a day', async () => {
  const state = await started();
  assert.equal(state.isStarted, true);
  assert.deepEqual(state.timers.later.map(([, ms]) => ms), [DESKTOP_UPDATE_FIRST_CHECK_MS]);
  assert.deepEqual(state.timers.repeats.map(([, ms]) => ms), [DESKTOP_UPDATE_EVERY_MS]);
  assert.equal(DESKTOP_UPDATE_EVERY_MS, 24 * 60 * 60 * 1000);
  assert.ok(DESKTOP_UPDATE_FIRST_CHECK_MS > 0 && DESKTOP_UPDATE_FIRST_CHECK_MS <= 30000);
  assert.deepEqual(state.asked, []);
  await runFirstCheck(state);
  assert.equal(state.asked.length, 1);
  await state.timers.repeats[0][0]();
  assert.equal(state.asked.length, 2);
});

test('a newer release shows one plain notice with a link to the release page and a dismiss button', async () => {
  const state = await runFirstCheck(await started());
  const notice = noticeIn(state.doc);
  assert.ok(notice);
  assert.equal(notice.attributes.role, 'status');
  assert.equal(notice.find('span').textContent, 'Version 1.2.0 is available');
  const link = notice.find('a');
  assert.equal(link.attributes.href, NEWER.url);
  assert.equal(link.attributes.target, '_blank');
  assert.equal(link.attributes.rel, 'noopener noreferrer');
  assert.equal(link.textContent, 'Open the release page');
  const dismiss = notice.find('button');
  assert.equal(dismiss.attributes.type, 'button');
  assert.equal(dismiss.textContent, 'Dismiss');
  await state.timers.repeats[0][0]();
  assert.equal(state.doc.box.children.length, 1);
});

test('dismissing hides the notice for that version only', async () => {
  const storage = storageFake();
  const state = await runFirstCheck(await started({ storage }));
  noticeIn(state.doc).find('button').listeners.click();
  assert.equal(noticeIn(state.doc), undefined);
  assert.equal(storage.values[DESKTOP_UPDATE_DISMISSED_KEY], '1.2.0');
  await state.timers.repeats[0][0]();
  assert.equal(noticeIn(state.doc), undefined);
  const later = await runFirstCheck(await started({ storage, answer: { ...NEWER, version: '1.3.0', url: `${RELEASE_PAGE}desktop-v1.3.0` } }));
  assert.equal(noticeIn(later.doc).find('span').textContent, 'Version 1.3.0 is available');
});

test('a newer release found later replaces the notice text instead of adding another', async () => {
  const doc = documentFake();
  const notice = createDesktopUpdateNotice({ document: doc, writeDismissed: () => {} });
  notice.show({ version: '1.2.0', url: NEWER.url });
  notice.show({ version: '1.3.0', url: `${RELEASE_PAGE}desktop-v1.3.0` });
  assert.equal(doc.box.children.length, 1);
  assert.equal(noticeIn(doc).find('span').textContent, 'Version 1.3.0 is available');
  assert.equal(noticeIn(doc).find('a').attributes.href, `${RELEASE_PAGE}desktop-v1.3.0`);
});

test('no notice when up to date, when the server has the check off, or when storage cannot be read', async () => {
  for (const answer of [{ ...NEWER, version: '1.1.0', url: `${RELEASE_PAGE}desktop-v1.1.0` }, { check: false, version: null, url: null }, { check: true, version: null, url: null }]) {
    const state = await runFirstCheck(await started({ answer }));
    assert.equal(noticeIn(state.doc), undefined, JSON.stringify(answer));
  }
  const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
  const state = await runFirstCheck(await started({ storage: broken }));
  assert.ok(noticeIn(state.doc));
  noticeIn(state.doc).find('button').listeners.click();
  assert.equal(noticeIn(state.doc), undefined);
});

test('a failed check shows nothing, logs one line and never throws', async () => {
  const state = await runFirstCheck(await started({ fail: new Error('the server answered 502') }));
  assert.equal(noticeIn(state.doc), undefined);
  assert.equal(state.logged.length, 1);
  assert.match(state.logged[0], /the server answered 502/);
  assert.doesNotMatch(state.logged[0], /\u2014/);
});

test('the page wiring asks the server route and is loaded after the logic', () => {
  const wiring = fs.readFileSync(path.join(STATIC, 'desktopUpdate.js'), 'utf8');
  assert.match(wiring, /fetch\('\/api\/desktop\/latest'\)/);
  assert.match(wiring, /startDesktopUpdateCheck\(/);
  assert.doesNotMatch(wiring, /api\.github\.com/);
  const index = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const logic = index.indexOf('<script src="desktopUpdateLogic.js"></script>');
  const page = index.indexOf('<script src="desktopUpdate.js"></script>');
  assert.ok(logic > 0 && page > logic);
});
