'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.join(__dirname, '..', '..', 'static');
const { OPEN_FAILURE_REASONS, openStepText, openFailureText, runBrowserOpen, desktopBrowserAvailable, createOpenView } = require(path.join(STATIC, 'browserOpenLogic.js'));

const ADDRESS = 'https://example.org/docs';
const BROWSER_OPEN = { id: 'browser.open', version: 1, available: true, browsers: [{ name: 'Firefox', state: 'connected' }] };

class FakeElement {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.textContent = '';
    this.className = '';
  }

  append(...children) { this.children.push(...children); }
  set innerHTML(value) { throw new Error(`innerHTML was set to ${value}`); }
}

function documentFake() {
  return { createElement: (tag) => new FakeElement(tag), body: new FakeElement('body') };
}

function linkFake({ connected = true, capabilities = [BROWSER_OPEN], status, open = { ok: true, browser: 'Firefox', tab: 12 } } = {}) {
  const calls = [];
  return {
    calls,
    linkStatus: async () => {
      calls.push('status');
      if (status instanceof Error) throw status;
      return { connected, capabilities };
    },
    openInBrowser: async (url, browser) => {
      calls.push(['open', url, browser]);
      if (open instanceof Error) throw open;
      return open;
    },
    lookAtScreen: async () => { throw new Error('an open must never look at the screen'); },
  };
}

function apiFake({ claim = { url: ADDRESS, browser: 'Firefox' }, answerOk = true } = {}) {
  const answers = [];
  const claims = [];
  return {
    answers,
    claims,
    claim: async (id) => { claims.push(id); return claim; },
    answer: async (id, outcome) => { answers.push([id, outcome]); return answerOk; },
  };
}

async function run({ link, api, request = { request_id: 'abc123', url: 'https://evil.example/', browser: 'Chrome' } }) {
  const shown = [];
  await runBrowserOpen({ request, link, api, show: (step) => shown.push(step) });
  return shown;
}

test('an open claims first, then says so, waits for Arty, opens what the server staged and answers it', async () => {
  const link = linkFake();
  const api = apiFake();
  const shown = await run({ link, api });
  assert.deepEqual(api.claims, ['abc123']);
  assert.deepEqual(shown.map((step) => step.kind), ['opening', 'waiting', 'done']);
  assert.equal(shown[0].text, `Opening ${ADDRESS} in Firefox on your PC...`);
  assert.equal(shown[1].text, 'Waiting for you to allow it in Arty');
  assert.equal(shown[2].text, `Opened ${ADDRESS} in Firefox`);
  assert.deepEqual(link.calls, ['status', ['open', ADDRESS, 'Firefox']]);
  assert.deepEqual(api.answers, [['abc123', { ok: true, browser: 'Firefox', tab: 12 }]]);
});

test('with no browser named the lines say your browser, and the done line names the one Arty used', async () => {
  const shown = await run({ link: linkFake({ open: { ok: true, browser: 'Chrome 2', tab: null } }), api: apiFake({ claim: { url: ADDRESS, browser: '' } }) });
  assert.equal(shown[0].text, `Opening ${ADDRESS} in your browser on your PC...`);
  assert.equal(shown[2].text, `Opened ${ADDRESS} in Chrome 2`);
});

test('the order is claim, line, status check, wait line, open, then the answer', async () => {
  const order = [];
  const link = {
    linkStatus: async () => { order.push('status'); return { connected: true, capabilities: [BROWSER_OPEN] }; },
    openInBrowser: async () => { order.push('open'); return { ok: false, reason: 'declined', error: 'You said no.' }; },
  };
  const api = { claim: async () => { order.push('claim'); return { url: ADDRESS, browser: '' }; }, answer: async () => { order.push('answer'); return true; } };
  await runBrowserOpen({ request: { request_id: 'r' }, link, api, show: (step) => order.push(step.kind) });
  assert.deepEqual(order, ['claim', 'opening', 'status', 'waiting', 'open', 'answer', 'failed']);
});

test('nothing runs without a claim', async () => {
  for (const claim of [null, false, {}, { url: 5, browser: '' }, { url: ADDRESS, browser: 7 }, 'https://example.org/']) {
    const link = linkFake();
    const api = apiFake({ claim });
    const shown = await run({ link, api });
    assert.deepEqual(shown, [], JSON.stringify(claim));
    assert.deepEqual(link.calls, []);
    assert.deepEqual(api.answers, []);
  }
});

test('each way of not opening gets a clear line and the server is told, so the model can say so', async () => {
  const cases = [
    ['declined', /You said no in Arty, so I did not open https:\/\/example\.org\/docs/],
    ['timed_out', /Nobody allowed it in Arty in time/],
    ['not_running', /Arty Desktop is not running/],
    ['not_linked', /not linked to Arty Desktop/],
    ['no_browser', /No browser is connected to Arty/],
    ['which_browser', /Could not open .*: which one\?/],
    ['abandoned', /Could not open/],
    ['busy', /Could not open/],
    ['bad_request', /Could not open/],
    ['not_connected', /Could not open/],
    ['unavailable', /Could not open/],
    ['failed', /Could not open/],
  ];
  for (const [reason, pattern] of cases) {
    const api = apiFake();
    const shown = await run({ link: linkFake({ open: { ok: false, reason, error: reason === 'which_browser' ? 'which one?' : 'it failed' } }), api });
    const last = shown[shown.length - 1];
    assert.equal(last.kind, 'failed', reason);
    assert.match(last.text, pattern, reason);
    assert.equal(api.answers.length, 1, reason);
    assert.equal(api.answers[0][1].ok, false);
    assert.equal(api.answers[0][1].reason, reason);
  }
  assert.deepEqual([...OPEN_FAILURE_REASONS].sort(), ['abandoned', 'bad_request', 'busy', 'declined', 'failed', 'no_browser', 'not_connected', 'not_linked', 'not_running', 'timed_out', 'unavailable', 'which_browser']);
});

test('a reason the page does not know is sent as failed', async () => {
  const api = apiFake();
  await run({ link: linkFake({ open: { ok: false, reason: 'made_up', error: 'x' } }), api });
  assert.equal(api.answers[0][1].reason, 'failed');
});

test('a link that is not connected, or that cannot open pages, stops before Arty is asked to open anything', async () => {
  const notConnected = linkFake({ connected: false });
  const api = apiFake();
  const shown = await run({ link: notConnected, api });
  assert.deepEqual(shown.map((step) => step.kind), ['opening', 'failed']);
  assert.match(shown[1].text, /Arty Desktop is not running/);
  assert.deepEqual(notConnected.calls, ['status']);
  assert.equal(api.answers[0][1].reason, 'not_running');
  for (const capabilities of [[], [{ ...BROWSER_OPEN, available: false }], [{ id: 'screen.look', version: 1, available: true }], null]) {
    const link = linkFake({ capabilities });
    const unavailable = apiFake();
    await run({ link, api: unavailable });
    assert.deepEqual(link.calls, ['status'], JSON.stringify(capabilities));
    assert.equal(unavailable.answers[0][1].reason, 'unavailable');
  }
});

test('no bridge at all, or a bridge that throws, is a failure the model hears about', async () => {
  for (const link of [null, linkFake({ status: new Error('boom') }), linkFake({ open: new Error('boom') })]) {
    const api = apiFake();
    const shown = await run({ link, api });
    assert.equal(shown[shown.length - 1].kind, 'failed');
    assert.equal(api.answers.length, 1);
    assert.equal(api.answers[0][1].ok, false);
  }
});

test('if the server cannot be told, the line does not claim the page was opened for the model', async () => {
  const shown = await run({ link: linkFake(), api: apiFake({ answerOk: false }) });
  const last = shown[shown.length - 1];
  assert.equal(last.kind, 'failed');
  assert.doesNotMatch(last.text, /^Opened/);
  assert.match(last.text, /could not tell starfire/);
});

test('what the page sends the server is short and only the known fields', async () => {
  const failing = apiFake();
  await run({ link: linkFake({ open: { ok: false, reason: 'failed', error: 'x'.repeat(900), page: 'secret' } }), api: failing });
  assert.deepEqual(Object.keys(failing.answers[0][1]).sort(), ['detail', 'ok', 'reason']);
  assert.ok(failing.answers[0][1].detail.length <= 300);
  const opened = apiFake();
  await run({ link: linkFake({ open: { ok: true, browser: 'x'.repeat(90), tab: 'twelve', title: 'secret page text' } }), api: opened });
  assert.deepEqual(opened.answers[0][1], { ok: true, browser: 'x'.repeat(40), tab: null });
});

test('the desktop link offers opening only when the bridge says browser.open is available', async () => {
  assert.equal(await desktopBrowserAvailable(null), false);
  assert.equal(await desktopBrowserAvailable(undefined), false);
  assert.equal(await desktopBrowserAvailable(linkFake()), true);
  assert.equal(await desktopBrowserAvailable(linkFake({ connected: false })), false);
  assert.equal(await desktopBrowserAvailable(linkFake({ capabilities: [{ ...BROWSER_OPEN, available: false }] })), false);
  assert.equal(await desktopBrowserAvailable(linkFake({ capabilities: [{ ...BROWSER_OPEN, available: 'yes' }] })), false);
  assert.equal(await desktopBrowserAvailable(linkFake({ capabilities: 'browser.open' })), false);
  assert.equal(await desktopBrowserAvailable(linkFake({ status: new Error('boom') })), false);
  assert.equal(await desktopBrowserAvailable({ linkStatus: async () => ({ connected: true }) }), false);
});

test('texts', () => {
  assert.equal(openStepText('opening', ADDRESS, 'Firefox'), `Opening ${ADDRESS} in Firefox on your PC...`);
  assert.equal(openStepText('waiting', ADDRESS, 'Firefox'), 'Waiting for you to allow it in Arty');
  assert.equal(openStepText('done', ADDRESS, ''), `Opened ${ADDRESS} in your browser`);
  assert.equal(openFailureText('declined', ADDRESS, 'Firefox', ''), `You said no in Arty, so I did not open ${ADDRESS}`);
});

test('the view keeps one line per open, updates it in place, marks failures and sets text only', () => {
  const document = documentFake();
  const output = new FakeElement('div');
  const view = createOpenView({ document, output, scrollToEnd: () => {} });
  view.show('r1', { kind: 'opening', text: '<img src=x onerror=alert(1)>' });
  view.show('r2', { kind: 'opening', text: 'two' });
  view.show('r1', { kind: 'failed', text: 'You said no in Arty' });
  assert.equal(output.children.length, 2);
  assert.match(output.children[0].className, /browser-open/);
  assert.match(output.children[0].className, /err/);
  assert.equal(output.children[0].children[0].textContent, 'You said no in Arty');
  assert.match(output.children[1].className, /info/);
});

test('the chat tells the server about browser opening only when it is available, and starts an open when asked', () => {
  const send = fs.readFileSync(path.join(STATIC, 'chatSend.js'), 'utf8');
  assert.match(send, /desktopBrowser = await desktopBrowserOffered\(\)/);
  assert.match(send, /\.\.\.\(desktopBrowser \? \{ desktop_browser: true \} : \{\}\)/);
  assert.match(send, /obj\.browser_request\) \{\s*startBrowserOpen\(obj\.browser_request\)/);
  const page = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const order = ['screenLook.js', 'browserOpenLogic.js', 'browserOpen.js', 'chatSend.js'].map((name) => page.indexOf(`<script src="${name}"></script>`));
  assert.ok(order.every((place) => place > 0) && order[0] < order[1] && order[1] < order[2] && order[2] < order[3], order.join());
});

test('the page glue talks to the open routes only and its names do not clash with any other page script', () => {
  const glue = fs.readFileSync(path.join(STATIC, 'browserOpen.js'), 'utf8');
  const logic = fs.readFileSync(path.join(STATIC, 'browserOpenLogic.js'), 'utf8');
  assert.match(glue, /\/api\/browser-opens\//);
  assert.doesNotMatch(glue + logic, /screen-looks|lookAtScreen|linkComputer|linkPair|linkUnpair|runComputer|innerHTML/);
  const topLevel = (source) => new Set([...source.matchAll(/^(?:async )?(?:function|const|let|var) (\w+)/gm)].map((match) => match[1]));
  const page = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const others = [...page.matchAll(/<script src="([\w.]+\.js)"><\/script>/g)].map((match) => match[1]).filter((name) => !name.startsWith('browserOpen'));
  assert.ok(others.includes('screenLookLogic.js') && others.includes('chatSend.js'));
  const otherNames = new Set(others.flatMap((name) => [...topLevel(fs.readFileSync(path.join(STATIC, name), 'utf8'))]));
  const clashes = [...topLevel(glue), ...topLevel(logic)].filter((name) => otherNames.has(name));
  assert.deepEqual(clashes, []);
});
