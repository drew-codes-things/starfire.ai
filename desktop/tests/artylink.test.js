const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { createArtyLink, requestArty, LOOPBACK_HOST } = require('../src/artyLink');
const { DEFAULT_SETTINGS } = require('../src/settings');
const { isProofCall, proofAnswer } = require('./fakeProof');

const TOKEN = 'abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE';

function build({ settings = {}, replies = [] } = {}) {
  let current = { ...DEFAULT_SETTINGS, ...settings };
  const calls = [];
  const queue = [...replies];
  const link = createArtyLink({
    getSettings: () => current,
    setToken: (token) => { current = { ...current, artyToken: token }; return { ok: true }; },
    request: async (call) => {
      if (isProofCall(call)) return proofAnswer(call, current);
      calls.push(call);
      const next = queue.shift();
      if (next instanceof Error) throw next;
      return next || { status: 200, body: {} };
    },
  });
  return { link, calls, settings: () => current };
}

function refused() {
  const error = new Error('connect ECONNREFUSED 127.0.0.1:8765');
  error.code = 'ECONNREFUSED';
  return error;
}

test('off means no conversation with Arty at all', async () => {
  const { link, calls } = build({ settings: { artyLink: false, artyToken: TOKEN } });
  assert.deepEqual(await link.status(), { state: 'off', paired: true });
  assert.equal((await link.pair('123456')).ok, false);
  assert.equal((await link.tryLink()).ok, false);
  assert.equal(calls.length, 0);
});

test('on but never paired is not connected and asks for nothing', async () => {
  const { link, calls } = build({ settings: { artyLink: true } });
  assert.deepEqual(await link.status(), { state: 'unpaired', paired: false });
  assert.equal((await link.tryLink()).ok, false);
  assert.equal(calls.length, 0);
});

test('paired and answering is Connected, and says whether computer control is on', async () => {
  const { link, calls } = build({ settings: { artyLink: true, artyToken: TOKEN }, replies: [{ status: 200, body: { ok: true, computer: true } }, { status: 200, body: { ok: true, computer: false } }] });
  assert.deepEqual(await link.status(), { state: 'connected', paired: true, computer: true });
  assert.equal((await link.status()).computer, false);
  assert.deepEqual(calls[0], { method: 'GET', path: '/link/status', token: TOKEN, port: 8765, timeoutMs: 3000 });
});

test('Arty Desktop not running is shown as unreachable, not as an error', async () => {
  const { link } = build({ settings: { artyLink: true, artyToken: TOKEN }, replies: [refused()] });
  assert.deepEqual(await link.status(), { state: 'unreachable', paired: true });
});

test('a refused token after a good proof is shown, and the pairing is kept for the user to decide', async () => {
  const { link, settings } = build({ settings: { artyLink: true, artyToken: TOKEN }, replies: [{ status: 403, body: { error: 'x' } }] });
  assert.deepEqual(await link.status(), { state: 'unproven', paired: true });
  assert.equal(settings().artyToken, TOKEN);
});

test('any other answer is unreachable and keeps the token', async () => {
  const { link, settings } = build({ settings: { artyLink: true, artyToken: TOKEN }, replies: [{ status: 500, body: {} }] });
  assert.equal((await link.status()).state, 'unreachable');
  assert.equal(settings().artyToken, TOKEN);
});

test('pairing trims the code and sends only the app name and a share, never the code', async () => {
  const { link, calls } = build({ settings: { artyLink: true }, replies: [{ status: 403, body: { error: 'No code is showing.' } }] });
  await link.pair(' 123 456 ');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].path, '/link/pair/start');
  assert.equal(calls[0].token, undefined);
  assert.deepEqual(Object.keys(calls[0].body).sort(), ['name', 'share']);
  assert.equal(calls[0].body.name, 'starfire Desktop');
  assert.match(calls[0].body.share, /^[0-9a-f]{512}$/);
  assert.doesNotMatch(JSON.stringify(calls[0]), /123456|123 456/);
});

test('a code that is not six digits never leaves the app', async () => {
  const { link, calls } = build({ settings: { artyLink: true } });
  for (const bad of ['', '12345', '1234567', 'abcdef', '12-456', null, 123456, undefined, {}]) {
    const result = await link.pair(bad);
    assert.equal(result.ok, false, String(bad));
    assert.match(result.error, /6 digits/);
  }
  assert.equal(calls.length, 0);
});

test('Arty Desktop refusing a code shows its reason and stores nothing', async () => {
  const { link, settings } = build({ settings: { artyLink: true }, replies: [{ status: 403, body: { error: 'That code has expired. Make a new code in Arty Desktop.' } }] });
  const result = await link.pair('123456');
  assert.deepEqual(result, { ok: false, error: 'That code has expired. Make a new code in Arty Desktop.' });
  assert.equal(settings().artyToken, '');
});

test('pairing with Arty Desktop closed says so', async () => {
  const { link } = build({ settings: { artyLink: true }, replies: [refused()] });
  const result = await link.pair('123456');
  assert.equal(result.ok, false);
  assert.match(result.error, /not running/);
});

test('an answer that is not a pairing answer saves nothing and finishes nothing', async () => {
  for (const body of [{ token: 'F'.repeat(43) }, { session: 'x', share: 'y', proof: 'z' }, {}, { session: '0'.repeat(32), share: '00'.repeat(256), proof: '0'.repeat(64) }]) {
    const { link, settings, calls } = build({ settings: { artyLink: true }, replies: [{ status: 200, body }] });
    assert.equal((await link.pair('123456')).ok, false);
    assert.equal(settings().artyToken, '');
    assert.equal(calls.some((call) => call.path === '/link/pair/finish'), false);
  }
});

test('unpairing tells Arty Desktop and forgets the token, even when Arty Desktop is closed', async () => {
  const first = build({ settings: { artyLink: true, artyToken: TOKEN }, replies: [{ status: 200, body: { ok: true } }] });
  assert.deepEqual(await first.link.unpair(), { ok: true });
  assert.equal(first.calls[0].path, '/link/unlink');
  assert.equal(first.settings().artyToken, '');
  const second = build({ settings: { artyLink: false, artyToken: TOKEN }, replies: [refused()] });
  assert.deepEqual(await second.link.unpair(), { ok: true });
  assert.equal(second.settings().artyToken, '');
});

test('trying the link sends one fixed step with the token and comes back as a result', async () => {
  const { link, calls } = build({ settings: { artyLink: true, artyToken: TOKEN }, replies: [{ status: 200, body: { ok: true, result: 'Terminal, Firefox' } }] });
  const result = await link.tryLink();
  assert.deepEqual(result, { ok: true, result: 'Terminal, Firefox' });
  assert.equal(calls[0].path, '/link/computer');
  assert.equal(calls[0].token, TOKEN);
  assert.deepEqual(calls[0].body, { tool: 'computer_apps', arguments: {} });
  assert.ok(calls[0].timeoutMs >= 150000);
});

test('a no from the user comes back as declined, not as a failure to retry', async () => {
  const { link } = build({ settings: { artyLink: true, artyToken: TOKEN }, replies: [{ status: 403, body: { error: 'You said no.', declined: true } }] });
  assert.deepEqual(await link.tryLink(), { ok: false, declined: true, error: 'You said no.' });
});

test('Arty Desktop going away mid-step is reported plainly', async () => {
  const { link } = build({ settings: { artyLink: true, artyToken: TOKEN }, replies: [refused()] });
  const result = await link.tryLink();
  assert.equal(result.ok, false);
  assert.match(result.error, /not running/);
});

function serve(handler) {
  return new Promise((resolve) => {
    const seen = [];
    const server = http.createServer((request, response) => {
      seen.push({ method: request.method, url: request.url, headers: request.headers });
      handler(request, response);
    });
    server.listen(0, LOOPBACK_HOST, () => resolve({ server, seen, port: server.address().port }));
  });
}

test('the real client talks to 127.0.0.1 with the token header and no browser headers', async () => {
  const { server, seen, port } = await serve((request, response) => {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ ok: true }));
  });
  const answer = await requestArty({ method: 'GET', path: '/link/status', token: TOKEN, port, timeoutMs: 2000 });
  server.close();
  assert.deepEqual(answer, { status: 200, body: { ok: true } });
  assert.equal(seen[0].headers.host, `127.0.0.1:${port}`);
  assert.equal(seen[0].headers['x-arty-link'], TOKEN);
  assert.equal('origin' in seen[0].headers, false);
  assert.equal(Object.keys(seen[0].headers).some((name) => name.startsWith('sec-fetch')), false);
});

test('the real client sends a JSON body and no token when it has none', async () => {
  let received = '';
  const { server, seen, port } = await serve((request, response) => {
    request.on('data', (chunk) => { received += chunk; });
    request.on('end', () => response.end('{"token":"t"}'));
  });
  await requestArty({ method: 'POST', path: '/link/pair', body: { code: '123456' }, port, timeoutMs: 2000 });
  server.close();
  assert.deepEqual(JSON.parse(received), { code: '123456' });
  assert.equal('x-arty-link' in seen[0].headers, false);
  assert.equal(seen[0].headers['content-type'], 'application/json');
});

test('the real client does not follow redirects and refuses oversized or non-JSON answers', async () => {
  const redirect = await serve((request, response) => { response.statusCode = 302; response.setHeader('Location', 'http://192.168.77.5/'); response.end(); });
  await assert.rejects(() => requestArty({ method: 'GET', path: '/x', port: redirect.port, timeoutMs: 2000 }), /redirect/i);
  redirect.server.close();
  const huge = await serve((request, response) => response.end('x'.repeat(2_000_000)));
  await assert.rejects(() => requestArty({ method: 'GET', path: '/x', port: huge.port, timeoutMs: 2000 }), /too large/i);
  huge.server.close();
  const text = await serve((request, response) => response.end('not json'));
  await assert.rejects(() => requestArty({ method: 'GET', path: '/x', port: text.port, timeoutMs: 2000 }), /not understand/i);
  text.server.close();
});

test('the real client gives up on a silent Arty Desktop', async () => {
  const quiet = await serve(() => {});
  await assert.rejects(() => requestArty({ method: 'GET', path: '/x', port: quiet.port, timeoutMs: 150 }), (error) => error.code === 'ETIMEDOUT');
  quiet.server.closeAllConnections();
  quiet.server.close();
});

test('nothing but the loopback address is ever used', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'src', 'artyLink.js'), 'utf8');
  assert.equal(LOOPBACK_HOST, '127.0.0.1');
  assert.doesNotMatch(source, /https:|localhost|0\.0\.0\.0|fetch\(/);
});
