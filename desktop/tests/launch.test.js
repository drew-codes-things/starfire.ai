const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { parseLaunchArgs } = require('../src/launch');
const { isLocalPageUrl } = require('../src/navigation');
const { deliverQuestion } = require('../src/quickask');
const { pickKeyEvent } = require('../src/ipc');

test('launch flags are read from the command line', () => {
  assert.deepEqual(parseLaunchArgs(['/usr/bin/starfire']), { quickAsk: false, hidden: false });
  assert.deepEqual(parseLaunchArgs(['x', '--quick-ask']), { quickAsk: true, hidden: false });
  assert.deepEqual(parseLaunchArgs(['x', '--hidden']), { quickAsk: false, hidden: true });
  assert.deepEqual(parseLaunchArgs(undefined), { quickAsk: false, hidden: false });
});

test('only files directly inside the pages folder count as the app\'s own pages', () => {
  const pages = path.join(path.sep, 'opt', 'starfire', 'pages');
  assert.equal(isLocalPageUrl('file:///opt/starfire/pages/settings.html?x=1', pages), true);
  assert.equal(isLocalPageUrl('file:///opt/starfire/pages/sub/x.html', pages), false);
  assert.equal(isLocalPageUrl('file:///opt/starfire/pages/../main.js', pages), false);
  assert.equal(isLocalPageUrl('file:///opt/starfire/pagesx/settings.html', pages), false);
  assert.equal(isLocalPageUrl('file:///etc/passwd', pages), false);
  assert.equal(isLocalPageUrl('https://starfire.example.com/pages/settings.html', pages), false);
  assert.equal(isLocalPageUrl('', pages), false);
  assert.equal(isLocalPageUrl(undefined, pages), false);
});

test('a question is retried until the page is ready, then gives up', async () => {
  let calls = 0;
  const waits = [];
  const sent = await deliverQuestion({
    question: 'hi',
    run: async () => { calls += 1; return calls < 3 ? 'not-ready' : 'sent'; },
    wait: async (ms) => { waits.push(ms); },
    attempts: 5,
    intervalMs: 10,
  });
  assert.equal(sent, 'sent');
  assert.equal(calls, 3);
  assert.deepEqual(waits, [10, 10]);

  const never = await deliverQuestion({ question: 'hi', run: async () => 'not-ready', wait: async () => {}, attempts: 3, intervalMs: 1 });
  assert.equal(never, 'not-ready');

  const failing = await deliverQuestion({ question: 'hi', run: async () => { throw new Error('page is loading'); }, wait: async () => {}, attempts: 2, intervalMs: 1 });
  assert.equal(failing, 'not-ready');
});

test('only the key fields cross the bridge, as plain values', () => {
  const picked = pickKeyEvent({ code: 'KeyK', ctrlKey: true, altKey: 'yes', shiftKey: false, extra: { evil: true }, key: 'k' });
  assert.deepEqual(picked, { code: 'KeyK', ctrlKey: true, altKey: false, shiftKey: false, metaKey: false });
  assert.deepEqual(pickKeyEvent(null), { code: '', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false });
  assert.equal(pickKeyEvent({ code: 'x'.repeat(500) }).code.length, 32);
});
