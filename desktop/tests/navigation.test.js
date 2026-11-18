const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createNavigationGuard, isSafeExternalUrl, isPermissionAllowed, guardWebContents } = require('../src/navigation');

const ORIGIN = 'https://starfire.example.com';
const guard = createNavigationGuard(ORIGIN);

test('pages on the starfire origin are allowed', () => {
  for (const url of [ORIGIN, `${ORIGIN}/`, `${ORIGIN}/login.html?next=%2F`, `${ORIGIN}/api/health`, 'HTTPS://STARFIRE.EXAMPLE.COM/x']) {
    assert.equal(guard.decide(url), 'allow', url);
  }
});

test('other web origins open in the system browser', () => {
  const urls = [
    'https://example.org/',
    'https://starfire.example.com.evil.com/',
    'https://evilstarfire.example.com/',
    `${ORIGIN}@evil.com/`,
    'https://evil.com/?u=https://starfire.example.com',
    'http://starfire.example.com/',
    'https://starfire.example.com:8443/',
    'https://sub.starfire.example.com/',
    'mailto:someone@example.com',
  ];
  for (const url of urls) {
    assert.equal(guard.decide(url), 'external', url);
  }
});

test('dangerous or unparseable targets are denied outright', () => {
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,hi', 'about:blank', 'blob:https://starfire.example.com/abc', 'ftp://x.example.com', 'vbscript:x', 'chrome://gpu', '', 'not a url', undefined, null]) {
    assert.equal(guard.decide(url), 'deny', String(url));
  }
});

test('only web and mail links may be handed to the system', () => {
  assert.equal(isSafeExternalUrl('https://example.org/a'), true);
  assert.equal(isSafeExternalUrl('http://example.org/a'), true);
  assert.equal(isSafeExternalUrl('mailto:a@example.org'), true);
  for (const url of ['file:///etc/passwd', 'javascript:alert(1)', 'smb://host/share', 'ms-msdt:/x', 'steam://run/1', '', 'x']) {
    assert.equal(isSafeExternalUrl(url), false, url);
  }
});

test('permissions are granted only to the starfire origin and only for what it needs', () => {
  for (const permission of ['media', 'notifications', 'clipboard-sanitized-write', 'fullscreen']) {
    assert.equal(isPermissionAllowed(permission, `${ORIGIN}/`, ORIGIN), true, permission);
    assert.equal(isPermissionAllowed(permission, 'https://evil.com/', ORIGIN), false, permission);
  }
  for (const permission of ['geolocation', 'openExternal', 'clipboard-read', 'midi', 'hid', 'usb', 'serial', 'pointerLock']) {
    assert.equal(isPermissionAllowed(permission, `${ORIGIN}/`, ORIGIN), false, permission);
  }
});

function fakeContents() {
  const contents = new EventEmitter();
  contents.windowOpenHandler = null;
  contents.setWindowOpenHandler = (handler) => { contents.windowOpenHandler = handler; };
  return contents;
}

function fakeEvent() {
  return { prevented: false, preventDefault() { this.prevented = true; } };
}

test('guardWebContents stops navigation away and opens the link outside', () => {
  const contents = fakeContents();
  const opened = [];
  const loaded = [];
  guardWebContents(contents, guard, { openExternal: (url) => opened.push(url), loadInside: (url) => loaded.push(url) });

  const inside = fakeEvent();
  contents.emit('will-navigate', inside, `${ORIGIN}/chat`);
  assert.equal(inside.prevented, false);

  const outside = fakeEvent();
  contents.emit('will-navigate', outside, 'https://example.org/page');
  assert.equal(outside.prevented, true);
  assert.deepEqual(opened, ['https://example.org/page']);

  const evil = fakeEvent();
  contents.emit('will-navigate', evil, 'file:///etc/passwd');
  assert.equal(evil.prevented, true);
  assert.equal(opened.length, 1);
});

test('guardWebContents also guards redirects', () => {
  const contents = fakeContents();
  const opened = [];
  const loaded = [];
  guardWebContents(contents, guard, { openExternal: (url) => opened.push(url), loadInside: (url) => loaded.push(url) });
  const event = fakeEvent();
  contents.emit('will-redirect', event, 'https://example.org/');
  assert.equal(event.prevented, true);
  assert.deepEqual(opened, ['https://example.org/']);
});

test('guardWebContents never opens a new window; starfire links load in the same window', () => {
  const contents = fakeContents();
  const opened = [];
  const loaded = [];
  guardWebContents(contents, guard, { openExternal: (url) => opened.push(url), loadInside: (url) => loaded.push(url) });

  assert.deepEqual(contents.windowOpenHandler({ url: 'https://example.org/' }), { action: 'deny' });
  assert.deepEqual(contents.windowOpenHandler({ url: 'javascript:alert(1)' }), { action: 'deny' });
  assert.deepEqual(opened, ['https://example.org/']);

  assert.deepEqual(contents.windowOpenHandler({ url: `${ORIGIN}/chat` }), { action: 'deny' });
  assert.deepEqual(opened, ['https://example.org/']);
  assert.deepEqual(loaded, [`${ORIGIN}/chat`]);
});

test('guardWebContents denies webview attachment', () => {
  const contents = fakeContents();
  guardWebContents(contents, guard, { openExternal: () => {}, loadInside: () => {} });
  const event = fakeEvent();
  contents.emit('will-attach-webview', event);
  assert.equal(event.prevented, true);
});
