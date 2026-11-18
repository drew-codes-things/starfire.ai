'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { stepText, failureText, runLook, desktopScreenConnected, createLookView, LOOK_FAILURE_REASONS } = require(path.join(__dirname, '..', '..', 'static', 'screenLookLogic.js'));

class FakeElement {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.textContent = '';
    this.className = '';
    this.parent = null;
    this.listeners = {};
    this.src = '';
  }

  append(...children) {
    for (const child of children) child.parent = this;
    this.children.push(...children);
  }

  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
}

function documentFake() {
  return { createElement: (tag) => new FakeElement(tag), body: new FakeElement('body') };
}

function linkFake({ connected = true, status, look = { ok: true, image: 'QUJD', mime: 'image/jpeg', target: 'your whole screen', window: 'Discord', note: '' } } = {}) {
  const calls = [];
  return {
    calls,
    linkStatus: async () => {
      calls.push('status');
      if (status instanceof Error) throw status;
      return { connected };
    },
    lookAtScreen: async (app) => {
      calls.push(['look', app]);
      if (look instanceof Error) throw look;
      return look;
    },
  };
}

function apiFake({ claim = { app: 'Discord' }, answerOk = true } = {}) {
  const answers = [];
  return {
    answers,
    claim: async () => claim,
    answer: async (id, outcome) => { answers.push([id, outcome]); return answerOk; },
  };
}

async function run({ link, api, app = 'Discord' }) {
  const shown = [];
  await runLook({ request: { request_id: 'abc123', app }, link, api, show: (step) => shown.push(step) });
  return shown;
}

test('looking at an app says so, then says it is waiting for Arty, then says it looked with the picture', async () => {
  const link = linkFake();
  const api = apiFake();
  const shown = await run({ link, api });
  assert.deepEqual(shown.map((step) => step.kind), ['looking', 'waiting', 'done']);
  assert.equal(shown[0].text, 'Looking at Discord on your PC...');
  assert.equal(shown[1].text, 'Waiting for you to allow it in Arty');
  assert.equal(shown[2].text, 'Looked at Discord');
  assert.deepEqual(shown[2].picture, { image: 'QUJD', mime: 'image/jpeg' });
  assert.deepEqual(link.calls, ['status', ['look', 'Discord']]);
  assert.equal(api.answers.length, 1);
  assert.equal(api.answers[0][0], 'abc123');
  assert.equal(api.answers[0][1].ok, true);
  assert.equal(api.answers[0][1].image, 'QUJD');
});

test('with no app it is the screen', async () => {
  const shown = await run({ link: linkFake(), api: apiFake({ claim: { app: '' } }), app: '' });
  assert.equal(shown[0].text, 'Looking at your screen on your PC...');
  assert.equal(shown[2].text, 'Looked at your screen');
});

test('the line is shown before anything is asked of Arty, and the wait line comes only after the connection check', async () => {
  const order = [];
  const link = { linkStatus: async () => { order.push('status'); return { connected: true }; }, lookAtScreen: async () => { order.push('look'); return { ok: false, reason: 'declined', error: 'You said no.' }; } };
  const api = apiFake();
  await runLook({ request: { request_id: 'r', app: 'Discord' }, link, api, show: (step) => order.push(step.kind) });
  assert.deepEqual(order, ['looking', 'status', 'waiting', 'look', 'failed']);
});

test('a request someone else already took up is left alone', async () => {
  const link = linkFake();
  const shown = await run({ link, api: apiFake({ claim: null }) });
  assert.deepEqual(shown, []);
  assert.deepEqual(link.calls, []);
});

test('each way of not looking gets a clear line and the server is told, so the model can say so', async () => {
  const cases = [
    ['declined', /said no in Arty.*did not look at Discord/],
    ['timed_out', /Nobody allowed it in Arty in time/],
    ['not_running', /Arty Desktop is not running/],
    ['not_linked', /not linked to Arty Desktop/],
    ['not_found', /Could not look at Discord: .*no window/i],
    ['unavailable', /Could not look at Discord: computer control is off/],
    ['cannot_see', /Could not look at Discord/],
    ['off_limits', /Could not look at Discord/],
    ['busy', /Could not look at Discord/],
    ['failed', /Could not look at Discord/],
  ];
  for (const [reason, pattern] of cases) {
    const api = apiFake();
    const shown = await run({ link: linkFake({ look: { ok: false, reason, error: reason === 'not_found' ? 'There is no window for Discord open, so nothing was captured.' : reason === 'unavailable' ? 'computer control is off' : 'it failed' } }), api });
    const last = shown[shown.length - 1];
    assert.equal(last.kind, 'failed', reason);
    assert.match(last.text, pattern, reason);
    assert.equal(last.picture, undefined);
    assert.equal(api.answers.length, 1, reason);
    assert.equal(api.answers[0][1].ok, false);
    assert.equal(api.answers[0][1].reason, reason);
    assert.equal(api.answers[0][1].image, undefined);
  }
  assert.deepEqual([...LOOK_FAILURE_REASONS].sort(), ['busy', 'cannot_see', 'declined', 'failed', 'not_found', 'not_linked', 'not_running', 'off_limits', 'timed_out', 'unavailable'].sort());
});

test('a link that is not connected stops before Arty is asked anything', async () => {
  const link = linkFake({ connected: false });
  const api = apiFake();
  const shown = await run({ link, api });
  assert.deepEqual(shown.map((step) => step.kind), ['looking', 'failed']);
  assert.match(shown[1].text, /Arty Desktop is not running/);
  assert.deepEqual(link.calls, ['status']);
  assert.equal(api.answers[0][1].reason, 'not_running');
});

test('no bridge at all, or a bridge that throws, is a failure the model hears about', async () => {
  for (const link of [null, linkFake({ status: new Error('boom') }), linkFake({ look: new Error('boom') })]) {
    const api = apiFake();
    const shown = await run({ link, api });
    assert.equal(shown[shown.length - 1].kind, 'failed');
    assert.equal(api.answers.length, 1);
    assert.equal(api.answers[0][1].ok, false);
  }
});

test('if the picture cannot be handed to the server the line says so and does not claim it looked', async () => {
  const shown = await run({ link: linkFake(), api: apiFake({ answerOk: false }) });
  const last = shown[shown.length - 1];
  assert.equal(last.kind, 'failed');
  assert.doesNotMatch(last.text, /^Looked at/);
  assert.match(last.text, /could not be given to starfire/);
});

test('what the page sends the server about a failure is short plain text', async () => {
  const api = apiFake();
  await run({ link: linkFake({ look: { ok: false, reason: 'failed', error: 'x'.repeat(900) } }), api });
  assert.ok(api.answers[0][1].detail.length <= 300);
});

test('the desktop link counts as connected only when the bridge says so', async () => {
  assert.equal(await desktopScreenConnected(null), false);
  assert.equal(await desktopScreenConnected(undefined), false);
  assert.equal(await desktopScreenConnected(linkFake({ connected: true })), true);
  assert.equal(await desktopScreenConnected(linkFake({ connected: false })), false);
  assert.equal(await desktopScreenConnected(linkFake({ status: new Error('boom') })), false);
  assert.equal(await desktopScreenConnected({ linkStatus: async () => ({ connected: 'yes' }) }), false);
});

test('texts', () => {
  assert.equal(stepText('looking', 'Discord'), 'Looking at Discord on your PC...');
  assert.equal(stepText('waiting', 'Discord'), 'Waiting for you to allow it in Arty');
  assert.equal(stepText('done', ''), 'Looked at your screen');
  assert.equal(failureText('declined', 'Discord', ''), 'You said no in Arty, so I did not look at Discord');
});

test('the view keeps one line per look, updates it in place and ends with a thumbnail that opens larger', () => {
  const document = documentFake();
  const output = new FakeElement('div');
  const view = createLookView({ document, output, scrollToEnd: () => {} });
  view.show('r1', { kind: 'looking', text: 'Looking at Discord on your PC...' });
  assert.equal(output.children.length, 1);
  const line = output.children[0];
  assert.match(line.className, /screen-look/);
  assert.equal(line.children[0].textContent, 'Looking at Discord on your PC...');
  view.show('r1', { kind: 'waiting', text: 'Waiting for you to allow it in Arty' });
  assert.equal(output.children.length, 1);
  assert.equal(line.children[0].textContent, 'Waiting for you to allow it in Arty');
  view.show('r1', { kind: 'done', text: 'Looked at Discord', picture: { image: 'QUJD', mime: 'image/jpeg' } });
  assert.equal(output.children.length, 1);
  assert.equal(line.children[0].textContent, 'Looked at Discord');
  const thumbnail = line.children.find((child) => child.tag === 'img');
  assert.equal(thumbnail.src, 'data:image/jpeg;base64,QUJD');
  assert.match(thumbnail.attributes.alt, /screenshot/i);
  thumbnail.listeners.click();
  const overlay = document.body.children[0];
  assert.match(overlay.className, /look-overlay/);
  assert.equal(overlay.children[0].src, 'data:image/jpeg;base64,QUJD');
  overlay.listeners.click();
  assert.equal(document.body.children.length, 0);
});

test('a failure line has no picture and is marked as an error', () => {
  const document = documentFake();
  const output = new FakeElement('div');
  const view = createLookView({ document, output, scrollToEnd: () => {} });
  view.show('r2', { kind: 'looking', text: 'Looking at Discord on your PC...' });
  view.show('r2', { kind: 'failed', text: 'You said no in Arty, so I did not look at Discord' });
  const line = output.children[0];
  assert.match(line.className, /err/);
  assert.equal(line.children.some((child) => child.tag === 'img'), false);
});

test('two looks keep their own lines', () => {
  const document = documentFake();
  const output = new FakeElement('div');
  const view = createLookView({ document, output, scrollToEnd: () => {} });
  view.show('a', { kind: 'looking', text: 'one' });
  view.show('b', { kind: 'looking', text: 'two' });
  view.show('a', { kind: 'waiting', text: 'one again' });
  assert.deepEqual(output.children.map((line) => line.children[0].textContent), ['one again', 'two']);
});

test('a picture that is not a data url of an image is never shown', () => {
  const document = documentFake();
  const output = new FakeElement('div');
  const view = createLookView({ document, output, scrollToEnd: () => {} });
  view.show('r3', { kind: 'done', text: 'Looked at x', picture: { image: '"><script>', mime: 'text/html' } });
  assert.equal(output.children[0].children.some((child) => child.tag === 'img'), false);
});

test('the chat tells the server about the desktop link only when it is connected, and starts a look when asked', () => {
  const fs = require('node:fs');
  const send = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'chatSend.js'), 'utf8');
  assert.match(send, /\.\.\.\(desktopScreen \? \{ desktop_screen: true \} : \{\}\)/);
  assert.match(send, /desktopScreen = await desktopScreenOffered\(\)/);
  assert.match(send, /obj\.screen_request\) \{\s*startScreenLook\(obj\.screen_request\)/);
  const page = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'index.html'), 'utf8');
  const order = ['screenLookLogic.js', 'screenLook.js', 'chatSend.js'].map((name) => page.indexOf(`<script src="${name}"></script>`));
  assert.ok(order.every((place) => place > 0) && order[0] < order[1] && order[1] < order[2], order.join());
});

test('the page glue talks to the look routes only and offers nothing but a status check and a look', () => {
  const fs = require('node:fs');
  const glue = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'screenLook.js'), 'utf8');
  assert.deepEqual([...glue.matchAll(/starfireDesktopLink\.(\w+)/g)].map((match) => match[1]), []);
  assert.doesNotMatch(glue, /linkComputer|linkPair|linkUnpair|runComputer/);
  assert.match(glue, /\/api\/screen-looks\//);
});
