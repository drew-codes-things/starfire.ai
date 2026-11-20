const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const http = require('node:http');
const spake2 = require('../src/spake2');
const { createArtyLink, LOOPBACK_HOST } = require('../src/artyLink');
const { DEFAULT_SETTINGS } = require('../src/settings');

const OFFER = '0123456789abcdef0123456789abcdef';
const SAVED_TOKEN = 'REALTOKEN_rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr';
const MIN_GAP_MS = 30000;

function send(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(body));
}

function fakeArty({ password = OFFER } = {}) {
  const state = { fingerprint: null, session: null };
  const seen = [];
  return new Promise((resolve) => {
    const server = http.createServer((request, response) => {
      let text = '';
      request.on('data', (chunk) => { text += chunk; });
      request.on('end', () => {
        const url = new URL(request.url, 'http://127.0.0.1');
        const body = text ? JSON.parse(text) : null;
        seen.push({ path: url.pathname, body, token: request.headers['x-arty-link'] || null });
        if (url.pathname === '/link/pair/start') {
          const secret = spake2.newSecret();
          const share = spake2.shareHex(secret, password, spake2.ARTY);
          let key;
          try {
            key = spake2.sharedKey(secret, password, spake2.ARTY, body.share, share);
          } catch (error) {
            send(response, 403, { error: 'bad share' });
            return;
          }
          state.session = { id: crypto.randomBytes(16).toString('hex'), key };
          send(response, 200, { session: state.session.id, share, proof: spake2.proof(key, spake2.ARTY) });
          return;
        }
        if (url.pathname === '/link/pair/finish') {
          if (!state.session || body.proof !== spake2.proof(state.session.key, spake2.APP)) {
            send(response, 403, { error: 'That secret is not right.' });
            return;
          }
          state.fingerprint = crypto.createHash('sha256').update(spake2.linkToken(state.session.key)).digest('hex');
          send(response, 200, { ok: true });
          return;
        }
        if (url.pathname === '/link/proof' && state.fingerprint) {
          const nonce = url.searchParams.get('nonce');
          send(response, 200, { proof: crypto.createHmac('sha256', state.fingerprint).update(`arty-link-proof:${request.socket.localPort}:${nonce}`).digest('hex') });
          return;
        }
        if (url.pathname === '/link/status') {
          send(response, 200, { ok: true, computer: true });
          return;
        }
        send(response, 404, { error: 'nothing here' });
      });
    });
    server.listen(0, LOOPBACK_HOST, () => resolve({ server, seen, port: server.address().port }));
  });
}

function linkFor(port, { settings = {}, offer = OFFER } = {}) {
  let current = { ...DEFAULT_SETTINGS, artyLink: true, artyPort: port, ...settings };
  let clock = 1000000;
  const tokens = [];
  const reads = [];
  const link = createArtyLink({
    getSettings: () => current,
    setToken: (token, options = {}) => {
      tokens.push([token, options]);
      current = { ...current, artyToken: token, artyLinkedByItself: token !== '' && options.byItself === true };
      return { ok: true };
    },
    readOffer: () => { reads.push(true); return offer; },
    now: () => clock,
  });
  return { link, tokens, reads, settings: () => current, advance: (ms) => { clock += ms; } };
}

async function withArty(options, run) {
  const arty = await fakeArty(options);
  try {
    await run(arty);
  } finally {
    arty.server.close();
  }
}

test('with the offer file and the switch on, it links by itself with local set on both calls', async () => {
  await withArty({}, async (arty) => {
    const { link, tokens, settings } = linkFor(arty.port);
    assert.deepEqual(await link.linkByItself(), { ok: true });
    assert.ok(settings().artyToken.length >= 40);
    assert.deepEqual(tokens.map(([, options]) => options), [{ byItself: true }]);
    const start = arty.seen.find((record) => record.path === '/link/pair/start');
    const finish = arty.seen.find((record) => record.path === '/link/pair/finish');
    assert.deepEqual(Object.keys(start.body).sort(), ['local', 'name', 'share']);
    assert.equal(start.body.local, true);
    assert.deepEqual(Object.keys(finish.body).sort(), ['local', 'proof', 'session']);
    assert.equal(finish.body.local, true);
    for (const record of arty.seen) assert.doesNotMatch(JSON.stringify(record.body), new RegExp(OFFER));
    const status = await link.status();
    assert.equal(status.state, 'connected');
    assert.equal(status.linkedByItself, true);
  });
});

test('pairing with a typed code never says local and is not marked as linked by itself', async () => {
  await withArty({ password: '482913' }, async (arty) => {
    const { link, tokens, reads } = linkFor(arty.port);
    assert.deepEqual(await link.pair('482 913'), { ok: true });
    for (const record of arty.seen.filter((entry) => entry.path.startsWith('/link/pair/'))) {
      assert.equal('local' in record.body, false, record.path);
    }
    assert.deepEqual(tokens.map(([, options]) => options), [{ byItself: false }]);
    assert.equal((await link.status()).linkedByItself, false);
    assert.equal(reads.length, 0);
  });
});

test('when Arty does not prove it holds the same secret, no finish is sent and nothing is saved', async () => {
  await withArty({ password: 'ffffffffffffffffffffffffffffffff' }, async (arty) => {
    const { link, tokens, settings } = linkFor(arty.port);
    const result = await link.linkByItself();
    assert.equal(result.ok, false);
    assert.equal(settings().artyToken, '');
    assert.deepEqual(tokens, []);
    assert.deepEqual(arty.seen.map((record) => record.path), ['/link/pair/start']);
  });
});

test('no attempt while a link exists: the offer is not even read', async () => {
  await withArty({}, async (arty) => {
    const { link, reads } = linkFor(arty.port, { settings: { artyToken: SAVED_TOKEN } });
    assert.equal((await link.linkByItself()).ok, false);
    assert.deepEqual(arty.seen, []);
    assert.equal(reads.length, 0);
  });
});

test('no attempt with linking by itself switched off, or with the link switched off', async () => {
  for (const settings of [{ artyAutoLink: false }, { artyLink: false }]) {
    await withArty({}, async (arty) => {
      const { link, reads } = linkFor(arty.port, { settings });
      assert.equal((await link.linkByItself()).ok, false);
      assert.deepEqual(arty.seen, [], JSON.stringify(settings));
      assert.equal(reads.length, 0);
    });
  }
});

test('no offer file means nothing is sent', async () => {
  await withArty({}, async (arty) => {
    const { link, reads } = linkFor(arty.port, { offer: null });
    assert.equal((await link.linkByItself()).ok, false);
    assert.equal(reads.length, 1);
    assert.deepEqual(arty.seen, []);
  });
});

test('after a failed try it waits 30 seconds before trying again', async () => {
  await withArty({ password: 'ffffffffffffffffffffffffffffffff' }, async (arty) => {
    const { link, advance } = linkFor(arty.port);
    await link.linkByItself();
    await link.linkByItself();
    advance(MIN_GAP_MS - 1);
    await link.linkByItself();
    assert.equal(arty.seen.length, 1);
    advance(1);
    await link.linkByItself();
    assert.equal(arty.seen.length, 2);
  });
});

test('a try that found no offer does not hold back the next one', async () => {
  await withArty({}, async (arty) => {
    let offer = null;
    let current = { ...DEFAULT_SETTINGS, artyLink: true, artyPort: arty.port };
    const link = createArtyLink({
      getSettings: () => current,
      setToken: (token) => { current = { ...current, artyToken: token }; return { ok: true }; },
      readOffer: () => offer,
      now: () => 5,
    });
    assert.equal((await link.linkByItself()).ok, false);
    offer = OFFER;
    assert.deepEqual(await link.linkByItself(), { ok: true });
  });
});

test('two tries at once send one pairing', async () => {
  await withArty({}, async (arty) => {
    const { link } = linkFor(arty.port);
    const results = await Promise.all([link.linkByItself(), link.linkByItself()]);
    assert.equal(results.filter((result) => result.ok).length, 1);
    assert.equal(arty.seen.filter((record) => record.path === '/link/pair/start').length, 1);
  });
});

test('Arty Desktop not running is a plain failure and the next try waits', async () => {
  const closed = await fakeArty();
  const port = closed.port;
  await new Promise((resolve) => closed.server.close(resolve));
  const { link, settings } = linkFor(port);
  const result = await link.linkByItself();
  assert.equal(result.ok, false);
  assert.match(result.error, /not running/);
  assert.equal(settings().artyToken, '');
});

test('status says whether the link was made by itself only while it is paired', async () => {
  await withArty({}, async (arty) => {
    const off = linkFor(arty.port, { settings: { artyLink: false, artyToken: SAVED_TOKEN, artyLinkedByItself: true } });
    assert.equal((await off.link.status()).linkedByItself, true);
    const unpaired = linkFor(arty.port, { settings: { artyLinkedByItself: true } });
    assert.equal((await unpaired.link.status()).linkedByItself, false);
  });
});
