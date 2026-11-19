const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const spake2 = require('../src/spake2');
const { createArtyLink, LOOPBACK_HOST } = require('../src/artyLink');
const { DEFAULT_SETTINGS } = require('../src/settings');
const { registerIpc, CHANNELS } = require('../src/ipc');
const { createNavigationGuard, isPermissionAllowed, isLocalPageUrl, guardWebContents } = require('../src/navigation');
const { createGestureTracker } = require('../src/userGesture');
const { createWindowManager } = require('../src/windows');

const ROOT = path.join(__dirname, '..');
const SHARED_VECTOR = {
  appShareSha256_16: '4c78a39903b4aaba',
  artyShareSha256_16: 'e73bdcc2d4aa02fc',
  key: 'c3b78bd58e58e5d74a6888bd8aa84055e700ab0c3b271e59b77c7655d006c3ea',
  artyProof: '705b01fec2ca56f73afe7064e6d6150ac7bed859aa1859482fb14b8af6728422',
  appProof: '6f250c3523f9f2a38be275862b7c5671dd6287023f8d66018ba5b84526bdb8db',
  token: 'Df6qd57CBd3S165TVvBJBQQF5kTcttjGFLxBCK4zzr4',
};
const CODE = '482913';

function sha16(text) {
  return crypto.createHash('sha256').update(text).digest('hex').slice(0, 16);
}

test('the pairing math matches Arty Desktop\'s half on the shared vector', () => {
  assert.equal(spake2.PRIME, BigInt(`0x${crypto.getDiffieHellman('modp14').getPrime('hex')}`));
  const appShare = spake2.shareHex(0x1234567n, '123456', spake2.APP);
  const artyShare = spake2.shareHex(0x7654321n, '123456', spake2.ARTY);
  const key = spake2.sharedKey(0x1234567n, '123456', spake2.APP, appShare, artyShare);
  assert.deepEqual(key, spake2.sharedKey(0x7654321n, '123456', spake2.ARTY, appShare, artyShare));
  assert.equal(sha16(appShare), SHARED_VECTOR.appShareSha256_16);
  assert.equal(sha16(artyShare), SHARED_VECTOR.artyShareSha256_16);
  assert.equal(key.toString('hex'), SHARED_VECTOR.key);
  assert.equal(spake2.proof(key, spake2.ARTY), SHARED_VECTOR.artyProof);
  assert.equal(spake2.proof(key, spake2.APP), SHARED_VECTOR.appProof);
  assert.equal(spake2.linkToken(key), SHARED_VECTOR.token);
});

test('a share outside the group is refused', () => {
  const good = spake2.shareHex(5n, CODE, spake2.APP);
  for (const bad of ['', '00'.repeat(256), `${'00'.repeat(255)}01`, (spake2.PRIME - 1n).toString(16).padStart(512, '0'), 'zz'.repeat(256), good.toUpperCase(), 7]) {
    assert.throws(() => spake2.sharedKey(5n, CODE, spake2.APP, good, bad), String(bad).slice(0, 20));
  }
});

function readBody(request) {
  return new Promise((resolve) => {
    let text = '';
    request.on('data', (chunk) => { text += chunk; });
    request.on('end', () => resolve(text));
  });
}

function send(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(body));
}

function listen(handler) {
  return new Promise((resolve) => {
    const seen = [];
    const server = http.createServer(async (request, response) => {
      const body = await readBody(request);
      const record = { method: request.method, url: request.url, token: request.headers['x-arty-link'] || null, body };
      seen.push(record);
      handler(request, response, record);
    });
    server.listen(0, LOOPBACK_HOST, () => resolve({ server, seen, port: server.address().port }));
  });
}

function fakeArty({ code = CODE, reportedPort } = {}) {
  const state = { fingerprint: null, session: null };
  return listen((request, response, record) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname === '/link/pair/start') {
      const sent = JSON.parse(record.body);
      const secret = spake2.newSecret();
      const share = spake2.shareHex(secret, code, spake2.ARTY);
      const key = spake2.sharedKey(secret, code, spake2.ARTY, sent.share, share);
      state.session = { id: crypto.randomBytes(16).toString('hex'), key };
      send(response, 200, { session: state.session.id, share, proof: spake2.proof(key, spake2.ARTY) });
      return;
    }
    if (url.pathname === '/link/pair/finish') {
      const sent = JSON.parse(record.body);
      if (!state.session || sent.proof !== spake2.proof(state.session.key, spake2.APP)) {
        send(response, 403, { error: 'That code is not right.' });
        return;
      }
      state.fingerprint = crypto.createHash('sha256').update(spake2.linkToken(state.session.key)).digest('hex');
      send(response, 200, { ok: true });
      return;
    }
    if (url.pathname === '/link/proof') {
      if (!state.fingerprint) {
        send(response, 404, { error: 'Nothing is linked', linked: false });
        return;
      }
      const port = reportedPort || request.socket.localPort;
      const nonce = url.searchParams.get('nonce');
      send(response, 200, { proof: crypto.createHmac('sha256', state.fingerprint).update(`arty-link-proof:${port}:${nonce}`).digest('hex') });
      return;
    }
    const known = record.token && crypto.createHash('sha256').update(record.token).digest('hex') === state.fingerprint;
    if (!known) {
      send(response, 403, { error: 'not allowed' });
      return;
    }
    if (url.pathname === '/link/status') send(response, 200, { ok: true, computer: true });
    else if (url.pathname === '/link/computer') send(response, 200, { ok: true, result: 'Terminal, Firefox' });
    else send(response, 200, { ok: true });
  });
}

function reviewersFakeListener() {
  return listen((request, response) => {
    if (request.url === '/link/pair') send(response, 200, { token: 'F'.repeat(43) });
    else if (request.url.startsWith('/link/pair/start')) send(response, 200, { token: 'F'.repeat(43), ok: true });
    else send(response, 200, { ok: true, computer: true, result: 'fake result from the squatter', proof: 'f'.repeat(64) });
  });
}

function linkFor(port, settings = {}) {
  let current = { ...DEFAULT_SETTINGS, artyLink: true, artyPort: port, ...settings };
  const link = createArtyLink({
    getSettings: () => current,
    setToken: (token) => { current = { ...current, artyToken: token }; return { ok: true }; },
  });
  return { link, settings: () => current };
}

const SAVED_TOKEN = 'REALTOKEN_rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr';

test('pairing with the real kind of Arty proves both sides and saves a token that never crossed the wire', async () => {
  const arty = await fakeArty();
  const { link, settings } = linkFor(arty.port);
  assert.deepEqual(await link.pair('482 913'), { ok: true });
  assert.ok(settings().artyToken.length >= 40);
  for (const record of arty.seen) {
    assert.doesNotMatch(record.body, /482913|482 913/);
    assert.ok(!record.body.includes(settings().artyToken));
  }
  assert.deepEqual(await link.status(), { state: 'connected', paired: true, computer: true });
  const statusCalls = arty.seen.slice(-2);
  assert.match(statusCalls[0].url, /^\/link\/proof\?nonce=[0-9a-f]{32}$/);
  assert.equal(statusCalls[0].token, null);
  assert.equal(statusCalls[1].url, '/link/status');
  assert.equal((await link.tryLink()).result, 'Terminal, Firefox');
  arty.server.close();
});

test('a wrong code fails Arty\'s proof and nothing is saved', async () => {
  const arty = await fakeArty({ code: '111111' });
  const { link, settings } = linkFor(arty.port);
  const result = await link.pair('482913');
  assert.equal(result.ok, false);
  assert.match(result.error, /did not prove/);
  assert.equal(settings().artyToken, '');
  assert.equal(arty.seen.some((record) => record.url === '/link/pair/finish'), false);
  arty.server.close();
});

test('the reviewer\'s fake listener gets no code and no token, and the light never says Connected', async () => {
  const squatter = await reviewersFakeListener();
  const { link, settings } = linkFor(squatter.port);
  const paired = await link.pair('123 456');
  assert.equal(paired.ok, false);
  assert.equal(settings().artyToken, '');
  const fresh = linkFor(squatter.port, { artyToken: SAVED_TOKEN });
  const status = await fresh.link.status();
  assert.notEqual(status.state, 'connected');
  assert.equal(status.state, 'unproven');
  assert.equal((await fresh.link.tryLink()).ok, false);
  assert.equal((await fresh.link.lookAtScreen('Discord')).ok, false);
  await fresh.link.unpair();
  for (const record of squatter.seen) {
    assert.equal(record.token, null, record.url);
    assert.doesNotMatch(record.body, /123456|123 456|REALTOKEN/);
    assert.doesNotMatch(record.url, /REALTOKEN/);
  }
  assert.deepEqual([...new Set(squatter.seen.map((record) => record.url.split('?')[0]))].sort(), ['/link/pair/start', '/link/proof']);
  squatter.server.close();
});

test('a 403 from whatever answers does not forget the pairing', async () => {
  const forbidding = await listen((request, response) => send(response, 403, { error: 'no' }));
  const { link, settings } = linkFor(forbidding.port, { artyToken: SAVED_TOKEN });
  assert.deepEqual(await link.status(), { state: 'unproven', paired: true });
  assert.equal(settings().artyToken, SAVED_TOKEN);
  forbidding.server.close();
});

test('a proof made for another port is not accepted', async () => {
  const arty = await fakeArty({ reportedPort: 1 });
  const { link } = linkFor(arty.port);
  assert.deepEqual(await link.pair('482913'), { ok: true });
  assert.equal((await link.status()).state, 'unproven');
  assert.equal(arty.seen.filter((record) => record.token !== null).length, 0);
  arty.server.close();
});

test('the general step channel is gone; only a fixed try with no arguments remains', () => {
  assert.equal('linkComputer' in CHANNELS, false);
  assert.equal(CHANNELS.linkTry, 'starfire:link-try');
  const preload = fs.readFileSync(path.join(ROOT, 'preload.js'), 'utf8');
  assert.doesNotMatch(preload, /linkComputer|link-computer/);
  assert.match(preload, /linkTry: \(\) => call\('starfire:link-try'\)/);
  const source = fs.readFileSync(path.join(ROOT, 'src', 'artyLink.js'), 'utf8');
  assert.doesNotMatch(source, /runComputer/);
});

function ipcSetup({ local = true, settingsPage = true } = {}) {
  const handlers = new Map();
  const calls = [];
  registerIpc({
    ipcMain: { handle: (channel, handler) => handlers.set(channel, handler) },
    controller: { get: () => ({ artyLink: true, artyToken: SAVED_TOKEN }), update: () => ({ ok: true, settings: {} }), connect: async () => ({ ok: true }) },
    isLocalSender: () => local,
    isSettingsSender: () => local && settingsPage,
    describeKey: () => ({ ok: true }),
    actions: {
      shortcutProblem: () => null,
      linkStatus: async () => { calls.push(['linkStatus']); return { state: 'connected' }; },
      linkPair: async (code) => { calls.push(['linkPair', code]); return { ok: true }; },
      linkUnpair: async () => { calls.push(['linkUnpair']); return { ok: true }; },
      linkTry: async (sender) => { calls.push(['linkTry', sender]); return { ok: true }; },
    },
  });
  return { handlers, calls };
}

test('the link channels answer only the settings page', async () => {
  const { handlers, calls } = ipcSetup({ settingsPage: false });
  for (const channel of [CHANNELS.linkStatus, CHANNELS.linkPair, CHANNELS.linkUnpair, CHANNELS.linkTry]) {
    await assert.rejects(() => handlers.get(channel)({ sender: 'quick-ask' }, {}), /Refused/, channel);
  }
  assert.deepEqual(calls, []);
});

test('the try channel passes only who asked, never anything the page sends', async () => {
  const { handlers, calls } = ipcSetup();
  await handlers.get(CHANNELS.linkTry)({ sender: 'settings-contents' }, { tool: 'computer_run_command', arguments: { command: 'id' } });
  assert.deepEqual(calls, [['linkTry', 'settings-contents']]);
});

test('a gesture counts once, and only for a short while', () => {
  let now = 1000;
  const tracker = createGestureTracker({ now: () => now });
  assert.equal(tracker.consume(), false);
  tracker.note({ type: 'mouseMove' });
  assert.equal(tracker.consume(), false);
  tracker.note({ type: 'mouseDown' });
  assert.equal(tracker.consume(), true);
  assert.equal(tracker.consume(), false);
  tracker.note({ type: 'keyDown' });
  now += 60000;
  assert.equal(tracker.consume(), false);
  const contents = new EventEmitter();
  tracker.watch(contents);
  contents.emit('input-event', {}, { type: 'rawKeyDown' });
  assert.equal(tracker.consume(), true);
});

const ORIGIN = 'https://starfire.example.com';

function fakeContents() {
  const contents = new EventEmitter();
  contents.setWindowOpenHandler = (handler) => { contents.windowOpenHandler = handler; };
  return contents;
}

function navigation(url, isMainFrame) {
  return { url, isMainFrame, prevented: false, preventDefault() { this.prevented = true; } };
}

test('a blocked page opens the system browser only for the main frame and only after a click', () => {
  const contents = fakeContents();
  const opened = [];
  let clicks = 0;
  guardWebContents(contents, createNavigationGuard(ORIGIN), {
    openExternal: (url) => opened.push(url),
    loadInside: () => {},
    mayOpenExternal: () => { if (clicks === 0) return false; clicks -= 1; return true; },
  });
  const scripted = navigation('https://example.org/a', true);
  contents.emit('will-navigate', scripted, scripted.url, false, true);
  assert.equal(scripted.prevented, true);
  assert.deepEqual(opened, []);
  clicks = 1;
  const framed = navigation('https://example.org/frame', false);
  contents.emit('will-redirect', framed, framed.url, false, false);
  assert.equal(framed.prevented, true);
  assert.deepEqual(opened, []);
  const clicked = navigation('https://example.org/b', true);
  contents.emit('will-navigate', clicked, clicked.url, false, true);
  assert.deepEqual(opened, ['https://example.org/b']);
  const again = navigation('https://example.org/c', true);
  contents.emit('will-navigate', again, again.url, false, true);
  assert.deepEqual(opened, ['https://example.org/b']);
  assert.deepEqual(contents.windowOpenHandler({ url: 'https://example.org/popup' }), { action: 'deny' });
  assert.deepEqual(opened, ['https://example.org/b']);
});

test('the starfire origin gets the microphone but never the camera', () => {
  assert.equal(isPermissionAllowed('media', `${ORIGIN}/`, ORIGIN, { mediaTypes: ['audio'] }), true);
  assert.equal(isPermissionAllowed('media', `${ORIGIN}/`, ORIGIN, { mediaType: 'audio' }), true);
  for (const details of [{ mediaTypes: ['video'] }, { mediaTypes: ['audio', 'video'] }, { mediaTypes: [] }, { mediaType: 'video' }, { mediaType: 'unknown' }, {}, undefined]) {
    assert.equal(isPermissionAllowed('media', `${ORIGIN}/`, ORIGIN, details), false, JSON.stringify(details));
  }
  assert.equal(isPermissionAllowed('notifications', `${ORIGIN}/`, ORIGIN), true);
});

test('an app page only counts when the file URL has no host (localhost is the same as none in a file URL)', () => {
  const pages = '/opt/app/resources/app.asar/pages';
  assert.equal(isLocalPageUrl(`file://${pages}/settings.html`, pages), true);
  assert.equal(isLocalPageUrl(`file://evilhost${pages}/settings.html`, pages), false);
  assert.equal(isLocalPageUrl(`file://localhost${pages}/settings.html`, pages), true);
  assert.equal(isLocalPageUrl(`file://127.0.0.1${pages}/settings.html`, pages), false);
});

function windowsWithFakes() {
  const created = [];
  class FakeWindow {
    constructor(options) {
      this.options = options;
      this.webContents = fakeContents();
      this.webContents.scripts = [];
      this.webContents.loadURL = () => {};
      this.webContents.send = () => {};
      this.webContents.executeJavaScript = async (script) => { this.webContents.scripts.push(script); return 'sent'; };
      created.push(this);
    }
    on() {}
    loadURL() {}
    loadFile() {}
    removeMenu() {}
    show() {}
    focus() {}
    hide() {}
    isMinimized() { return false; }
    isVisible() { return false; }
    isFocused() { return true; }
    close() {}
  }
  const electron = { BrowserWindow: FakeWindow, Notification: { isSupported: () => false }, screen: {}, shell: {}, app: { quit() {} } };
  const controller = { get: () => ({ ...DEFAULT_SETTINGS, address: ORIGIN }) };
  const manager = createWindowManager({ electron, controller, pagesDir: '/app/pages', preloadPath: '/app/preload.js', webPreloadPath: '/app/preload-web.js', iconPath: '/app/icon.png' });
  return { manager, created };
}

test('a quick ask is cleaned in the main process: no objects, no empty text, at most the cap', async () => {
  const { manager, created } = windowsWithFakes();
  assert.equal((await manager.submitQuickAsk({ a: 1 })).ok, false);
  assert.equal((await manager.submitQuickAsk('   ')).ok, false);
  assert.equal(created.length, 0);
  assert.equal((await manager.submitQuickAsk('x'.repeat(5000))).ok, true);
  const [script] = created[0].webContents.scripts;
  assert.ok(script.includes(`"${'x'.repeat(4000)}"`));
  assert.ok(!script.includes('x'.repeat(4001)));
});

test('only a click in the settings window lets it try the link, once', () => {
  const { manager, created } = windowsWithFakes();
  manager.showSettings();
  const settingsContents = created[0].webContents;
  assert.equal(manager.consumeGesture(settingsContents), false);
  settingsContents.emit('input-event', {}, { type: 'mouseDown' });
  assert.equal(manager.consumeGesture({}), false);
  assert.equal(manager.consumeGesture(settingsContents), true);
  assert.equal(manager.consumeGesture(settingsContents), false);
});

function answeringProofWith(proof) {
  return listen((request, response) => {
    if (request.url.startsWith('/link/proof')) send(response, 200, { proof });
    else send(response, 200, { ok: true, computer: true, result: 'fake result' });
  });
}

async function withListener(started, check) {
  const listener = await started;
  try {
    await check(listener);
  } finally {
    listener.server.closeAllConnections();
    listener.server.close();
  }
}

const ODD_PROOFS = ['\u00e9'.repeat(64), '\u00e9'.repeat(32), 'F'.repeat(64), 'f'.repeat(63), `${'f'.repeat(63)}\n`, ['f'.repeat(64)], 64, null];

test('a proof that is not 64 lowercase hex is unproven, never a crash or Node\'s own words', async () => {
  for (const proof of ODD_PROOFS) {
    await withListener(answeringProofWith(proof), async (odd) => {
      const { link } = linkFor(odd.port, { artyToken: SAVED_TOKEN });
      assert.deepEqual(await link.status(), { state: 'unproven', paired: true }, String(proof));
      const tried = await link.tryLink();
      assert.match(tried.error, /did not prove/);
      const looked = await link.lookAtScreen('Discord');
      assert.equal(looked.reason, 'not_linked');
      assert.match(looked.error, /did not prove/);
      assert.equal(odd.seen.filter((record) => record.token !== null).length, 0);
    });
  }
});

test('a pairing answer whose proof is not 64 lowercase hex is refused as not proven', async () => {
  for (const proof of ODD_PROOFS) {
    const started = listen((request, response) => {
      const share = spake2.shareHex(spake2.newSecret(), CODE, spake2.ARTY);
      send(response, 200, { session: crypto.randomBytes(16).toString('hex'), share, proof });
    });
    await withListener(started, async (odd) => {
      const { link, settings } = linkFor(odd.port);
      const result = await link.pair(CODE);
      assert.equal(result.ok, false);
      assert.match(result.error, /did not prove/, String(proof));
      assert.equal(settings().artyToken, '');
    });
  }
});

test('an unexpected error inside the link is reported in fixed words, not Node\'s', async () => {
  const link = createArtyLink({
    getSettings: () => ({ ...DEFAULT_SETTINGS, artyLink: true, artyToken: SAVED_TOKEN, artyPort: 8765 }),
    setToken: () => ({ ok: true }),
    request: async () => { throw new TypeError('Input buffers must have the same byte length'); },
  });
  const tried = await link.tryLink();
  const looked = await link.lookAtScreen('Discord');
  const paired = await link.pair(CODE);
  for (const error of [tried.error, looked.error, paired.error]) {
    assert.equal(typeof error, 'string');
    assert.doesNotMatch(error, /Input buffers|byte length/);
  }
  assert.equal(looked.reason, 'failed');
});

test('while pairing, words from an unproven listener are never shown; a fixed sentence is', async () => {
  const replies = [[403, { error: 'Wrong code. Type your starfire password in the box below instead.' }],
    [400, { error: 'Type your password here.' }], [500, { error: 'password please' }], [404, {}]];
  for (const [status, body] of replies) {
    await withListener(listen((request, response) => send(response, status, body)), async (liar) => {
      const { link, settings } = linkFor(liar.port);
      const result = await link.pair(CODE);
      assert.equal(result.ok, false);
      assert.doesNotMatch(result.error, /password|Type your/);
      assert.match(result.error, /not accepted/);
      assert.equal(settings().artyToken, '');
    });
  }
});
