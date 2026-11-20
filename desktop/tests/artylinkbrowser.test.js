const test = require('node:test');
const assert = require('node:assert/strict');
const { createArtyLink } = require('../src/artyLink');
const { DEFAULT_SETTINGS } = require('../src/settings');
const { isProofCall, proofAnswer } = require('./fakeProof');

const TOKEN = 'abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE';
const ADDRESS = 'https://example.org/docs?page=2#top';
const GOOD_CAPABILITIES = [
  { id: 'screen.look', version: 1, available: true },
  { id: 'browser.open', version: 1, available: true, browsers: [{ name: 'Firefox', state: 'connected' }, { name: 'Chrome 2', state: 'connected' }] },
  { id: 'browser.own', version: 2, available: false },
  { id: 'screen.control', version: 1, available: false },
  { id: 'quiz', version: 1, available: true },
];

function build({ settings = {}, replies = [], proven = true } = {}) {
  const current = { ...DEFAULT_SETTINGS, artyLink: true, artyToken: TOKEN, ...settings };
  const calls = [];
  const queue = [...replies];
  const link = createArtyLink({
    getSettings: () => current,
    setToken: () => ({ ok: true }),
    request: async (call) => {
      if (isProofCall(call)) return proven ? proofAnswer(call, current) : { status: 200, body: { proof: '0'.repeat(64) } };
      calls.push(call);
      const next = queue.shift();
      if (next instanceof Error) throw next;
      return next;
    },
  });
  return { link, calls };
}

function statusWith(body) {
  return build({ replies: [{ status: 200, body: { ok: true, computer: true, ...body } }] }).link.status();
}

function refused() {
  return Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:8765'), { code: 'ECONNREFUSED' });
}

test('a good capability list and version are kept as Arty Desktop sent them', async () => {
  const status = await statusWith({ arty_desktop: '1.6.0', capabilities: GOOD_CAPABILITIES });
  assert.equal(status.state, 'connected');
  assert.equal(status.artyDesktop, '1.6.0');
  assert.deepEqual(status.capabilities, [
    { id: 'screen.look', version: 1, available: true },
    { id: 'browser.open', version: 1, available: true, browsers: [{ name: 'Firefox', state: 'connected' }, { name: 'Chrome 2', state: 'connected' }] },
    { id: 'browser.own', version: 2, available: false },
    { id: 'screen.control', version: 1, available: false },
    { id: 'quiz', version: 1, available: true },
  ]);
});

test('every state that is not connected has no capabilities and no version', async () => {
  for (const settings of [{ artyLink: false }, { artyToken: '' }]) {
    const status = await build({ settings }).link.status();
    assert.deepEqual(status.capabilities, []);
    assert.equal(status.artyDesktop, '');
  }
  assert.deepEqual((await build({ replies: [refused()] }).link.status()).capabilities, []);
  assert.deepEqual((await build({ replies: [{ status: 403, body: { capabilities: GOOD_CAPABILITIES } }] }).link.status()).capabilities, []);
  assert.deepEqual((await build({ proven: false }).link.status()).capabilities, []);
});

test('an old Arty Desktop that sends no capabilities is connected with none', async () => {
  const status = await statusWith({});
  assert.equal(status.state, 'connected');
  assert.deepEqual(status.capabilities, []);
  assert.equal(status.artyDesktop, '');
});

test('malformed capability entries are dropped one by one and the good ones stay', async () => {
  const bad = [
    { id: 'shell.run', version: 1, available: true },
    { id: 'screen.look ', version: 1, available: true },
    { id: 5, version: 1, available: true },
    { id: 'quiz', version: 0, available: true },
    { id: 'quiz', version: -1, available: true },
    { id: 'quiz', version: 1.5, available: true },
    { id: 'quiz', version: '1', available: true },
    { id: 'quiz', version: 1, available: 'true' },
    { id: 'quiz', version: 1, available: 1 },
    { id: 'quiz', version: 1 },
    { id: 'screen.look', version: 1, available: true, browsers: [{ name: 'Firefox', state: 'connected' }] },
    { id: 'browser.open', version: 1, available: true, browsers: 'Firefox' },
    { id: 'browser.open', version: 1, available: true, browsers: Array.from({ length: 11 }, () => ({ name: 'Firefox', state: 'connected' })) },
    null, 'screen.look', ['screen.look'], 7,
  ];
  for (let start = 0; start < bad.length; start += 9) {
    const status = await statusWith({ capabilities: [...bad.slice(start, start + 9), { id: 'screen.look', version: 1, available: true }] });
    assert.deepEqual(status.capabilities, [{ id: 'screen.look', version: 1, available: true }], `entries from ${start}`);
  }
});

test('a list longer than ten, or one that is not a list, gives no capabilities at all', async () => {
  const eleven = Array.from({ length: 11 }, () => ({ id: 'quiz', version: 1, available: true }));
  assert.deepEqual((await statusWith({ capabilities: eleven })).capabilities, []);
  for (const capabilities of [{ id: 'quiz', version: 1, available: true }, 'screen.look', 5, null]) {
    assert.deepEqual((await statusWith({ capabilities })).capabilities, [], JSON.stringify(capabilities));
  }
});

test('the same capability twice is kept once', async () => {
  const status = await statusWith({ capabilities: [{ id: 'quiz', version: 1, available: true }, { id: 'quiz', version: 2, available: false }] });
  assert.deepEqual(status.capabilities, [{ id: 'quiz', version: 1, available: true }]);
});

test('only known browser names that are connected are kept, and unknown fields never pass through', async () => {
  const browsers = [
    { name: 'Firefox', state: 'connected' },
    { name: 'Edge 9', state: 'connected', secret: 'x' },
    { name: 'Netscape', state: 'connected' },
    { name: 'Firefox 1', state: 'connected' },
    { name: 'Firefox 10', state: 'connected' },
    { name: 'Chrome  2', state: 'connected' },
    { name: 'firefox', state: 'connected' },
    { name: 'Fire‮fox', state: 'connected' },
    { name: 'Firefox\n', state: 'connected' },
    { name: 'Fire​fox', state: 'connected' },
    { name: 'Brave', state: 'waiting' },
    { name: 'Opera' },
    { name: 5, state: 'connected' },
    'Vivaldi',
    null,
  ];
  for (const batch of [browsers.slice(0, 8), [browsers[0], ...browsers.slice(8)]]) {
    const status = await statusWith({ capabilities: [{ id: 'browser.open', version: 1, available: true, browsers: batch, note: '<b>hi</b>' }] });
    const expected = batch.includes(browsers[1]) ? [{ name: 'Firefox', state: 'connected' }, { name: 'Edge 9', state: 'connected' }] : [{ name: 'Firefox', state: 'connected' }];
    assert.deepEqual(status.capabilities, [{ id: 'browser.open', version: 1, available: true, browsers: expected }]);
  }
});

test('browser.open with no browsers list is kept with an empty one', async () => {
  const status = await statusWith({ capabilities: [{ id: 'browser.open', version: 1, available: false }] });
  assert.deepEqual(status.capabilities, [{ id: 'browser.open', version: 1, available: false, browsers: [] }]);
});

test('a version of Arty Desktop that is not plain numbers is dropped', async () => {
  for (const version of ['1.6', '1.6.0-beta', 'v1.6.0', ' 1.6.0', '1.6.0\n', `${'1'.repeat(10)}.${'2'.repeat(5)}.${'3'.repeat(5)}`, 160, null, ['1.6.0']]) {
    assert.equal((await statusWith({ arty_desktop: version })).artyDesktop, '', JSON.stringify(version));
  }
  assert.equal((await statusWith({ arty_desktop: '12.0.31' })).artyDesktop, '12.0.31');
});

test('opening a page goes to the browser route only, with the token and exactly the action, address and browser', async () => {
  const { link, calls } = build({ replies: [{ status: 200, body: { ok: true, browser: 'Firefox', tab: 12, title: 'secret page text' } }] });
  const result = await link.openInBrowser(ADDRESS, 'Firefox');
  assert.deepEqual(result, { ok: true, browser: 'Firefox', tab: 12 });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].path, '/link/browser');
  assert.equal(calls[0].token, TOKEN);
  assert.deepEqual(calls[0].body, { action: 'open', url: ADDRESS, browser: 'Firefox' });
  assert.ok(calls[0].timeoutMs >= 150000);
});

test('no browser named sends an empty browser and an http address is allowed', async () => {
  const { link, calls } = build({ replies: [{ status: 200, body: { ok: true, browser: 'Chrome', tab: 0 } }] });
  assert.deepEqual(await link.openInBrowser('http://192.168.1.16/', ''), { ok: true, browser: 'Chrome', tab: 0 });
  assert.deepEqual(calls[0].body, { action: 'open', url: 'http://192.168.1.16/', browser: '' });
});

test('what Arty Desktop says it opened is checked before it is passed on', async () => {
  const { link } = build({ replies: [{ status: 200, body: { ok: true, browser: 'Fire‮fox', tab: 'twelve' } }, { status: 200, body: { ok: true, browser: 'Firefox', tab: -3 } }] });
  assert.deepEqual(await link.openInBrowser(ADDRESS, ''), { ok: true, browser: '', tab: null });
  assert.deepEqual(await link.openInBrowser(ADDRESS, ''), { ok: true, browser: 'Firefox', tab: null });
});

test('off or unpaired sends nothing and says it is not linked', async () => {
  for (const settings of [{ artyLink: false }, { artyToken: '' }]) {
    const { link, calls } = build({ settings });
    const result = await link.openInBrowser(ADDRESS, '');
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'not_linked');
    assert.equal(calls.length, 0);
  }
});

test('a site too long to show on the Allow card never leaves the app', async () => {
  const { link, calls } = build();
  const addresses = [
    `https://accounts.google.com.${'a'.repeat(1900)}.example/`,
    `https://${'a'.repeat(64)}.example/`,
    `https://${Array(5).fill('a'.repeat(60)).join('.')}.example/`,
  ];
  for (const url of addresses) {
    const result = await link.openInBrowser(url, '');
    assert.equal(result.ok, false, url.slice(0, 60));
    assert.equal(result.reason, 'bad_request');
  }
  assert.equal(calls.length, 0);
});

test('an address or browser that cannot be shown plainly on the Allow card never leaves the app', async () => {
  const { link, calls } = build();
  const addresses = [
    5, null, {}, [ADDRESS], '', 'example.org', 'ftp://example.org/', 'javascript:alert(1)', 'JavaScript:alert(1)',
    'data:text/html,hi', 'file:///etc/passwd', 'https://', `https://example.org/${'a'.repeat(2000)}`,
    'https://exam‮ple.org/', 'https://example.org/\npath', 'https://example.org/ path', 'https://exam​ple.org/',
    'https://example.org/\u0000', ' https://example.org/',
  ];
  for (const url of addresses) {
    const result = await link.openInBrowser(url, '');
    assert.equal(result.ok, false, JSON.stringify(url).slice(0, 60));
    assert.equal(result.reason, 'bad_request');
  }
  for (const browser of [5, null, {}, 'Netscape', 'firefox', 'Firefox 1', 'Firefox 10', 'Fire‮fox', 'Firefox\n', 'x'.repeat(41), ' Firefox']) {
    const result = await link.openInBrowser(ADDRESS, browser);
    assert.equal(result.ok, false, JSON.stringify(browser));
    assert.equal(result.reason, 'bad_request');
  }
  assert.equal(calls.length, 0);
});

test('an address exactly at the limit is sent', async () => {
  const url = `https://example.org/${'a'.repeat(2000 - 'https://example.org/'.length)}`;
  const { link, calls } = build({ replies: [{ status: 200, body: { ok: true, browser: 'Firefox', tab: 1 } }] });
  assert.equal((await link.openInBrowser(url, 'Firefox')).ok, true);
  assert.equal(calls[0].body.url, url);
});

test('nothing is posted when Arty Desktop does not prove itself first', async () => {
  const { link, calls } = build({ proven: false, replies: [{ status: 200, body: { ok: true, browser: 'Firefox', tab: 1 } }] });
  const result = await link.openInBrowser(ADDRESS, 'Firefox');
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'not_linked');
  assert.equal(calls.length, 0);
});

test('every refusal reason Arty Desktop can give is passed on with its words, and anything else becomes failed', async () => {
  const reasons = ['declined', 'timed_out', 'abandoned', 'busy', 'bad_request', 'no_browser', 'which_browser', 'not_connected', 'unavailable', 'failed'];
  const { link } = build({ replies: [...reasons, 'made-up', undefined].map((reason) => ({ status: 403, body: { error: `said ${reason}`, reason } })) });
  for (const reason of reasons) assert.deepEqual(await link.openInBrowser(ADDRESS, ''), { ok: false, reason, error: `said ${reason}` });
  assert.equal((await link.openInBrowser(ADDRESS, '')).reason, 'failed');
  assert.equal((await link.openInBrowser(ADDRESS, '')).reason, 'failed');
});

test('an ok without a 200, or a 200 without ok, is not an open', async () => {
  const { link } = build({ replies: [{ status: 202, body: { ok: true, browser: 'Firefox', tab: 1 } }, { status: 200, body: { browser: 'Firefox', tab: 1 } }] });
  assert.equal((await link.openInBrowser(ADDRESS, '')).ok, false);
  assert.equal((await link.openInBrowser(ADDRESS, '')).ok, false);
});

test('the error Arty Desktop gives is cleaned and short', async () => {
  const { link } = build({ replies: [{ status: 403, body: { error: `You said no.‮\n${'x'.repeat(900)}`, reason: 'declined' } }] });
  const result = await link.openInBrowser(ADDRESS, '');
  assert.ok(!result.error.includes('‮') && !result.error.includes('\n') && result.error.length <= 300);
});

test('Arty Desktop not running is reported as not running, other trouble as failed', async () => {
  const running = await build({ replies: [refused()] }).link.openInBrowser(ADDRESS, '');
  assert.equal(running.reason, 'not_running');
  assert.match(running.error, /not running/);
  const odd = await build({ replies: [Object.assign(new Error('x'), { code: 'EBADBODY' })] }).link.openInBrowser(ADDRESS, '');
  assert.equal(odd.reason, 'failed');
});
