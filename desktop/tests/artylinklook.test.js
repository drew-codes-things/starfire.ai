const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { createArtyLink, requestArty, LOOPBACK_HOST } = require('../src/artyLink');
const { DEFAULT_SETTINGS } = require('../src/settings');
const { isProofCall, proofAnswer } = require('./fakeProof');

const TOKEN = 'abcdefghijklmnopqrstuvwxyz0123456789_-ABCDE';
const JPEG = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1, 2, 3]).toString('base64');
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1]).toString('base64');
const PICTURE = { ok: true, image: JPEG, mime: 'image/jpeg', target: 'your whole screen', window: '#general - Discord', note: 'Discord is in front.' };

function build({ settings = {}, replies = [] } = {}) {
  const current = { ...DEFAULT_SETTINGS, artyLink: true, artyToken: TOKEN, ...settings };
  const calls = [];
  const queue = [...replies];
  const link = createArtyLink({
    getSettings: () => current,
    setToken: () => ({ ok: true }),
    request: async (call) => {
      if (isProofCall(call)) return proofAnswer(call, current);
      calls.push(call);
      const next = queue.shift();
      if (next instanceof Error) throw next;
      return next;
    },
  });
  return { link, calls };
}

function refused() {
  return Object.assign(new Error('connect ECONNREFUSED 127.0.0.1:8765'), { code: 'ECONNREFUSED' });
}

test('a look goes to the screenshot route only, with the token and just the app name', async () => {
  const { link, calls } = build({ replies: [{ status: 200, body: PICTURE }] });
  const result = await link.lookAtScreen('Discord');
  assert.deepEqual(result, { ok: true, image: JPEG, mime: 'image/jpeg', target: 'your whole screen', window: '#general - Discord', note: 'Discord is in front.' });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].path, '/link/screenshot');
  assert.equal(calls[0].token, TOKEN);
  assert.deepEqual(calls[0].body, { app: 'Discord' });
  assert.ok(calls[0].timeoutMs >= 150000);
  assert.ok(calls[0].maxBytes >= 4000000);
});

test('no app named means the whole screen and an empty app', async () => {
  const { link, calls } = build({ replies: [{ status: 200, body: PICTURE }] });
  await link.lookAtScreen('');
  assert.deepEqual(calls[0].body, { app: '' });
});

test('a look never uses the computer step route, whatever it is given', async () => {
  const { link, calls } = build({ replies: [{ status: 200, body: PICTURE }, { status: 200, body: PICTURE }] });
  await link.lookAtScreen('computer_run_command');
  await link.lookAtScreen('Discord');
  assert.deepEqual(calls.map((call) => call.path), ['/link/screenshot', '/link/screenshot']);
});

test('off or unpaired sends nothing and says it is not linked', async () => {
  for (const settings of [{ artyLink: false }, { artyToken: '' }]) {
    const { link, calls } = build({ settings });
    const result = await link.lookAtScreen('Discord');
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'not_linked');
    assert.equal(calls.length, 0);
  }
});

test('an app name that is not plain text never leaves the app', async () => {
  const { link, calls } = build();
  for (const app of [5, null, {}, ['Discord'], 'x'.repeat(81), 'Dis‮cord', 'Dis\ncord', 'Dis​cord', 'Dis\u0000cord', 'Dis⁦cord']) {
    const result = await link.lookAtScreen(app);
    assert.equal(result.ok, false, JSON.stringify(app));
    assert.equal(result.reason, 'bad_request');
  }
  assert.equal(calls.length, 0);
});

test('a no from the user is declined, with the reason Arty Desktop gave', async () => {
  const { link } = build({ replies: [{ status: 403, body: { error: 'You said no.', declined: true, reason: 'declined' } }] });
  assert.deepEqual(await link.lookAtScreen('Discord'), { ok: false, reason: 'declined', error: 'You said no.' });
});

test('no answer in time and an app with no window keep their own reasons', async () => {
  const { link } = build({ replies: [
    { status: 403, body: { error: 'Nobody answered in time, which counts as no.', declined: false, reason: 'timed_out' } },
    { status: 404, body: { error: 'There is no window for Slack open, so nothing was captured.', reason: 'not_found' } },
  ] });
  assert.equal((await link.lookAtScreen('Slack')).reason, 'timed_out');
  const missing = await link.lookAtScreen('Slack');
  assert.equal(missing.reason, 'not_found');
  assert.match(missing.error, /no window for Slack/);
});

test('every refusal reason Arty Desktop can give is passed on and anything else becomes failed', async () => {
  const reasons = ['declined', 'timed_out', 'not_found', 'cannot_see', 'off_limits', 'unavailable', 'busy', 'bad_request', 'ambiguous', 'changed', 'failed'];
  const { link } = build({ replies: [...reasons, 'made-up', undefined].map((reason) => ({ status: 403, body: { error: 'x', reason } })) });
  for (const reason of reasons) assert.equal((await link.lookAtScreen('')).reason, reason);
  assert.equal((await link.lookAtScreen('')).reason, 'failed');
  assert.equal((await link.lookAtScreen('')).reason, 'failed');
});

test('Arty Desktop not running is reported as not running', async () => {
  const { link } = build({ replies: [refused()] });
  const result = await link.lookAtScreen('Discord');
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'not_running');
  assert.match(result.error, /not running/);
});

test('a picture that is not a picture is not passed on', async () => {
  const bad = [
    { ...PICTURE, image: '' }, { ...PICTURE, image: 'not base64 !!' }, { ...PICTURE, mime: 'text/html' },
    { ...PICTURE, mime: 'image/png' }, { ...PICTURE, image: Buffer.from('GIF89a').toString('base64') },
    { ...PICTURE, image: 5 }, { ...PICTURE, image: JPEG.repeat(2000000) },
  ];
  const { link } = build({ replies: bad.map((body) => ({ status: 200, body })) });
  for (const body of bad) {
    const result = await link.lookAtScreen('Discord');
    assert.equal(result.ok, false, JSON.stringify(body).slice(0, 80));
    assert.equal(result.reason, 'failed');
    assert.equal(result.image, undefined);
  }
});

test('a png is passed on when it is one', async () => {
  const { link } = build({ replies: [{ status: 200, body: { ...PICTURE, image: PNG, mime: 'image/png' } }] });
  assert.equal((await link.lookAtScreen('')).mime, 'image/png');
});

test('only the known fields of the answer are passed on, and text is cleaned and short', async () => {
  const body = { ...PICTURE, window: 'Disc‮ord'.padEnd(900, 'x'), note: 'ok\nline', command: 'id', token: 'x' };
  const { link } = build({ replies: [{ status: 200, body }] });
  const result = await link.lookAtScreen('Discord');
  assert.deepEqual(Object.keys(result).sort(), ['image', 'mime', 'note', 'ok', 'target', 'window']);
  assert.ok(!result.window.includes('‮') && result.window.length <= 300);
  assert.ok(!result.note.includes('\n'));
});

function serve(handler) {
  return new Promise((resolve) => {
    const server = http.createServer(handler);
    server.listen(0, LOOPBACK_HOST, () => resolve({ server, port: server.address().port }));
  });
}

test('the real client takes a big picture only when it is told to', async () => {
  const body = JSON.stringify({ ok: true, image: 'A'.repeat(2000000) });
  const { server, port } = await serve((request, response) => { response.setHeader('Content-Type', 'application/json'); response.end(body); });
  try {
    await assert.rejects(() => requestArty({ method: 'GET', path: '/x', port, timeoutMs: 3000 }), /too large/);
    const answer = await requestArty({ method: 'GET', path: '/x', port, timeoutMs: 3000, maxBytes: 4000000 });
    assert.equal(answer.status, 200);
  } finally {
    server.close();
  }
});
