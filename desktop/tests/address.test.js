const test = require('node:test');
const assert = require('node:assert/strict');
const { normalizeAddress, checkStarfire, isLocalNetworkHost } = require('../src/address');

test('a bare domain becomes an https origin', () => {
  assert.deepEqual(normalizeAddress('starfire.example.com'), { ok: true, origin: 'https://starfire.example.com' });
});

test('path, query and fragment are dropped, port is kept', () => {
  assert.equal(normalizeAddress('https://x.example.com:8443/some/path?a=1#h').origin, 'https://x.example.com:8443');
});

test('surrounding whitespace is ignored and the host is lower cased', () => {
  assert.equal(normalizeAddress('  HTTPS://Star.Example.COM/  ').origin, 'https://star.example.com');
});

test('empty input is refused with a message', () => {
  for (const input of ['', '   ', undefined, null, 42]) {
    const result = normalizeAddress(input);
    assert.equal(result.ok, false);
    assert.match(result.error, /address/i);
  }
});

test('http to a public host is refused even with the box ticked', () => {
  for (const allowInsecure of [false, true]) {
    const result = normalizeAddress('http://starfire.example.com', { allowInsecure });
    assert.equal(result.ok, false);
    assert.match(result.error, /https/i);
  }
});

test('http to a local or LAN host needs the tick box', () => {
  const hosts = ['localhost:8080', '127.0.0.1:8080', '[::1]:8080', '192.168.77.5:8080', '10.0.0.4', '172.16.5.5', '172.31.255.1', 'mybox.local', '169.254.10.1', '[fd12:3456::1]', '[fe80::1]'];
  for (const host of hosts) {
    const refused = normalizeAddress(`http://${host}`);
    assert.equal(refused.ok, false, host);
    assert.match(refused.error, /tick/i);
    const accepted = normalizeAddress(`http://${host}`, { allowInsecure: true });
    assert.equal(accepted.ok, true, host);
    assert.equal(accepted.origin, new URL(`http://${host}`).origin);
  }
});

test('hosts that only look local are public and refused over http', () => {
  const hosts = ['192.168.77.5.evil.com', '10.0.0.1.nip.io', '172.32.0.1', '172.15.0.1', '192.169.0.1', 'localhost.evil.com', '8.8.8.8', 'notlocal'];
  for (const host of hosts) {
    const result = normalizeAddress(`http://${host}`, { allowInsecure: true });
    assert.equal(result.ok, false, host);
  }
});

test('isLocalNetworkHost agrees with the http rule', () => {
  assert.equal(isLocalNetworkHost('localhost'), true);
  assert.equal(isLocalNetworkHost('192.168.0.2'), true);
  assert.equal(isLocalNetworkHost('example.com'), false);
});

test('other schemes are refused', () => {
  for (const input of ['ftp://x.example.com', 'javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,hi', 'ws://x.example.com']) {
    assert.equal(normalizeAddress(input, { allowInsecure: true }).ok, false, input);
  }
});

test('an address with a username or password is refused', () => {
  assert.equal(normalizeAddress('https://user:pw@starfire.example.com').ok, false);
  assert.equal(normalizeAddress('https://user@starfire.example.com').ok, false);
});

test('garbage hosts are refused', () => {
  for (const input of ['https://', 'https://exa mple.com', 'https://%', 'http://[::1', 'a b']) {
    assert.equal(normalizeAddress(input, { allowInsecure: true }).ok, false, input);
  }
});

function fakeFetch(responder) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return responder(url, options);
  };
  fetchImpl.calls = calls;
  return fetchImpl;
}

function textResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, type: 'basic', text: async () => body };
}

test('a healthy starfire is recognised and its version reported', async () => {
  const fetchImpl = fakeFetch(() => textResponse('{"ok": true, "version": "1.9.2"}'));
  const result = await checkStarfire('https://starfire.example.com', { fetchImpl });
  assert.deepEqual(result, { ok: true, version: '1.9.2' });
  assert.equal(fetchImpl.calls[0].url, 'https://starfire.example.com/api/health');
  assert.equal(fetchImpl.calls[0].options.redirect, 'manual');
  assert.ok(fetchImpl.calls[0].options.signal);
});

test('an unreachable host gives a could-not-reach message', async () => {
  const fetchImpl = fakeFetch(() => { throw new TypeError('fetch failed'); });
  const result = await checkStarfire('https://nothing.example.com', { fetchImpl });
  assert.equal(result.ok, false);
  assert.match(result.error, /reach/i);
});

test('a timeout says so', async () => {
  const fetchImpl = fakeFetch(() => { const error = new Error('timed out'); error.name = 'TimeoutError'; throw error; });
  const result = await checkStarfire('https://slow.example.com', { fetchImpl });
  assert.equal(result.ok, false);
  assert.match(result.error, /in time|timed out/i);
});

test('a page that is not starfire is refused', async () => {
  const bodies = ['<html>hello</html>', '{"ok": false}', '{"version": "1"}', '[]', 'null', '{"ok": true}', '{"ok": true, "version": 3}', '{"ok": "true", "version": "1"}'];
  for (const body of bodies) {
    const result = await checkStarfire('https://other.example.com', { fetchImpl: fakeFetch(() => textResponse(body)) });
    assert.equal(result.ok, false, body);
    assert.match(result.error, /starfire/i);
  }
});

test('an error status or a redirect is refused', async () => {
  for (const status of [301, 302, 403, 404, 500]) {
    const result = await checkStarfire('https://other.example.com', { fetchImpl: fakeFetch(() => textResponse('{"ok": true, "version": "1"}', status)) });
    assert.equal(result.ok, false, String(status));
  }
  const opaque = await checkStarfire('https://other.example.com', { fetchImpl: fakeFetch(() => ({ ok: false, status: 0, type: 'opaqueredirect', text: async () => '' })) });
  assert.equal(opaque.ok, false);
});

test('an oversized answer is refused', async () => {
  const result = await checkStarfire('https://big.example.com', { fetchImpl: fakeFetch(() => textResponse(`{"ok": true, "version": "${'9'.repeat(10000)}"}`)) });
  assert.equal(result.ok, false);
});
