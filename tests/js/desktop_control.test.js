'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.join(__dirname, '..', '..', 'static');
const { CONTROL_FAILURE_REASONS, controlStepText, controlFailureText, runControlStep, desktopControlState, createControlView } = require(path.join(STATIC, 'desktopControlLogic.js'));

const SCREEN_CONTROL = { id: 'screen.control', version: 1, available: true };
const STAGED = { action: 'type', arguments: { text: 'skype', app: 'Discord' } };

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

function linkFake({ connected = true, capabilities = [SCREEN_CONTROL], status, step = { ok: true, result: 'Typed it.' } } = {}) {
  const calls = [];
  return {
    calls,
    linkStatus: async () => {
      calls.push('status');
      if (status instanceof Error) throw status;
      return { connected, capabilities };
    },
    controlStep: async (action, args) => {
      calls.push(['step', action, args]);
      if (step instanceof Error) throw step;
      return step;
    },
    lookAtScreen: async () => { throw new Error('a step must never look at the screen by itself'); },
    openInBrowser: async () => { throw new Error('a step must never open a page'); },
  };
}

function apiFake({ claim = STAGED, answerOk = true } = {}) {
  const answers = [];
  const claims = [];
  return {
    answers,
    claims,
    claim: async (id) => { claims.push(id); return claim; },
    answer: async (id, outcome) => { answers.push([id, outcome]); return answerOk; },
  };
}

async function run({ link, api, request = { request_id: 'abc123', action: 'key', arguments: { keys: 'ctrl+alt+t' } } }) {
  const shown = [];
  await runControlStep({ request, link, api, show: (step) => shown.push(step) });
  return shown;
}

test('a step claims first, says so, waits for Arty, runs what the server staged and answers with what Arty said', async () => {
  const link = linkFake();
  const api = apiFake();
  const shown = await run({ link, api });
  assert.deepEqual(api.claims, ['abc123']);
  assert.deepEqual(shown.map((step) => step.kind), ['working', 'waiting', 'done']);
  assert.equal(shown[0].text, 'Asking Arty to type "skype" on your PC...');
  assert.equal(shown[1].text, 'Waiting for you to allow it in Arty');
  assert.equal(shown[2].text, 'Arty answered (type "skype"): Typed it.');
  assert.deepEqual(link.calls, ['status', ['step', 'type', { text: 'skype', app: 'Discord' }]]);
  assert.deepEqual(api.answers, [['abc123', { ok: true, result: 'Typed it.' }]]);
});

test('the order is claim, line, status check, wait line, step, then the answer', async () => {
  const order = [];
  const link = {
    linkStatus: async () => { order.push('status'); return { connected: true, capabilities: [SCREEN_CONTROL] }; },
    controlStep: async () => { order.push('step'); return { ok: false, reason: 'declined', error: 'You said no.' }; },
  };
  const api = { claim: async () => { order.push('claim'); return STAGED; }, answer: async () => { order.push('answer'); return true; } };
  await runControlStep({ request: { request_id: 'r' }, link, api, show: (step) => order.push(step.kind) });
  assert.deepEqual(order, ['claim', 'working', 'status', 'waiting', 'step', 'answer', 'failed']);
});

test('nothing runs without a good claim', async () => {
  for (const claim of [null, false, {}, { action: 'run_command', arguments: { command: 'id' } }, { action: 'screenshot', arguments: {} },
    { action: 'apps' }, { action: 'apps', arguments: null }, { action: 'apps', arguments: [] }, { action: 5, arguments: {} }, 'apps']) {
    const link = linkFake();
    const api = apiFake({ claim });
    const shown = await run({ link, api });
    assert.deepEqual(shown, [], JSON.stringify(claim));
    assert.deepEqual(link.calls, []);
    assert.deepEqual(api.answers, []);
  }
});

test('each way a step does not happen gets a clear line and the server is told, so the model can say so', async () => {
  const cases = [
    ['declined', /You said no in Arty, so I did not press ctrl\+k/],
    ['timed_out', /Nobody allowed it in Arty in time/],
    ['unavailable', /Controlling your PC is turned off in Arty/],
    ['busy', /Arty was busy with another step/],
    ['not_running', /Arty Desktop is not running/],
    ['not_linked', /not linked to Arty Desktop/],
    ['abandoned', /Could not press ctrl\+k: said abandoned/],
    ['bad_request', /Could not press ctrl\+k/],
    ['failed', /Could not tell whether Arty did it \(press ctrl\+k\): said failed/],
  ];
  assert.deepEqual([...CONTROL_FAILURE_REASONS].sort(), cases.map(([reason]) => reason).sort());
  for (const [reason, line] of cases) {
    const api = apiFake({ claim: { action: 'key', arguments: { keys: 'ctrl+k' } } });
    const shown = await run({ link: linkFake({ step: { ok: false, reason, error: `said ${reason}` } }), api });
    assert.equal(shown.at(-1).kind, 'failed');
    assert.match(shown.at(-1).text, line);
    assert.deepEqual(api.answers, [['abc123', { ok: false, reason, detail: `said ${reason}` }]]);
  }
});

test('an unknown reason or a thrown bridge is a plain failure, never a success', async () => {
  const odd = apiFake();
  await run({ link: linkFake({ step: { ok: false, reason: 'weird', error: 'x' } }), api: odd });
  assert.deepEqual(odd.answers[0][1], { ok: false, reason: 'failed', detail: 'x' });
  const thrown = apiFake();
  await run({ link: linkFake({ step: new Error('ipc gone') }), api: thrown });
  assert.deepEqual(thrown.answers[0][1], { ok: false, reason: 'failed', detail: 'starfire Desktop could not reach Arty Desktop.' });
});

test('when Arty Desktop is not running, control is off, or the bridge is missing, no step is sent', async () => {
  const notRunning = linkFake({ connected: false });
  const offApi = apiFake();
  await run({ link: notRunning, api: offApi });
  assert.deepEqual(notRunning.calls, ['status']);
  assert.equal(offApi.answers[0][1].reason, 'not_running');
  const off = linkFake({ capabilities: [{ ...SCREEN_CONTROL, available: false }] });
  const offAnswers = apiFake();
  await run({ link: off, api: offAnswers });
  assert.deepEqual(off.calls, ['status']);
  assert.equal(offAnswers.answers[0][1].reason, 'unavailable');
  const missing = apiFake();
  await run({ link: undefined, api: missing });
  assert.equal(missing.answers[0][1].reason, 'not_linked');
});

test('a long result is cut to what the server takes, and a lost answer is said honestly', async () => {
  const api = apiFake();
  await run({ link: linkFake({ step: { ok: true, result: 'x'.repeat(5000) } }), api });
  assert.equal(api.answers[0][1].result.length, 4000);
  const lost = await run({ link: linkFake(), api: apiFake({ answerOk: false }) });
  assert.match(lost.at(-1).text, /Arty may have done it .*could not tell starfire/);
});

test('control is offered only while the link is connected and screen control is available in Arty', async () => {
  const offered = { offered: true, off: '' };
  assert.deepEqual(await desktopControlState(linkFake()), offered);
  assert.deepEqual(await desktopControlState(linkFake(), true), offered);
  assert.deepEqual(await desktopControlState(linkFake({ connected: false })), { offered: false, off: 'not_running' });
  assert.deepEqual(await desktopControlState(linkFake({ status: new Error('gone') })), { offered: false, off: 'not_running' });
  for (const capabilities of [[], [{ ...SCREEN_CONTROL, available: false }], [{ ...SCREEN_CONTROL, available: 'yes' }], [{ id: 'screen.look', version: 1, available: true }]]) {
    assert.deepEqual(await desktopControlState(linkFake({ capabilities })), { offered: false, off: 'screen_control_off' }, JSON.stringify(capabilities));
  }
});

test('outside a linked starfire Desktop the page says why only when the app told it the link is off', async () => {
  const older = { linkStatus: async () => ({ connected: true, capabilities: [SCREEN_CONTROL] }) };
  assert.deepEqual(await desktopControlState(older), { offered: false, off: 'desktop_outdated' });
  assert.deepEqual(await desktopControlState(undefined, true), { offered: false, off: 'not_linked' });
  for (const unlinked of [false, undefined, 'true', 1]) {
    assert.deepEqual(await desktopControlState(undefined, unlinked), { offered: false, off: '' }, String(unlinked));
  }
});

test('the lines name each step plainly and cut long text', () => {
  assert.equal(controlStepText('working', 'apps', {}), 'Asking Arty to list your open windows on your PC...');
  assert.equal(controlStepText('done', 'open', { app: 'Discord' }, "Couldn't open Discord: no app by that name."), "Arty answered (open Discord): Couldn't open Discord: no app by that name.");
  assert.equal(controlStepText('done', 'click', { target: 'the search box' }, 'Clicked.'), 'Arty answered (click the search box): Clicked.');
  assert.equal(controlStepText('done', 'click', { x: 10, y: 20 }, ''), 'Arty answered (click at 10, 20): nothing');
  assert.equal(controlStepText('done', 'scroll', {}, 'one\ntwo'), 'Arty answered (scroll down): one two');
  assert.equal(controlStepText('done', 'type', { text: 'y'.repeat(100) }, 'z'.repeat(200)), `Arty answered (type "${'y'.repeat(57)}..."): ${'z'.repeat(117)}...`);
  assert.ok(![['open', { app: 'Discord' }], ['apps', {}]].some(([action, args]) => controlStepText('done', action, args, 'x').includes('did it')));
  assert.equal(controlFailureText('declined', 'key', { keys: 'return' }, ''), 'You said no in Arty, so I did not press return');
  assert.equal(controlFailureText('failed', 'key', { keys: 'return' }, ''), 'Could not tell whether Arty did it (press return)');
  assert.doesNotMatch(controlFailureText('failed', 'apps', {}, 'The connection broke.'), /^Could not list|did not|nothing was done/i);
});

test('the view keeps one line per step, updates it in place, marks failures and sets text only', () => {
  const document = documentFake();
  const output = new FakeElement('div');
  const view = createControlView({ document, output, scrollToEnd: () => {} });
  view.show('r1', { kind: 'working', text: '<img src=x onerror=alert(1)>' });
  view.show('r2', { kind: 'working', text: 'two' });
  view.show('r1', { kind: 'failed', text: 'You said no in Arty' });
  assert.equal(output.children.length, 2);
  assert.match(output.children[0].className, /desktop-control/);
  assert.match(output.children[0].className, /err/);
  assert.equal(output.children[0].children[0].textContent, 'You said no in Arty');
  assert.match(output.children[1].className, /info/);
});

test('the chat tells the server about control only when it is available, and starts a step when asked', () => {
  const send = fs.readFileSync(path.join(STATIC, 'chatSend.js'), 'utf8');
  assert.match(send, /desktopControl = await desktopControlForThisChat\(\)/);
  assert.match(send, /\.\.\.\(desktopControl\.offered \? \{ desktop_control: true \} : \{\}\)/);
  assert.match(send, /\.\.\.\(desktopControl\.off \? \{ desktop_control_off: desktopControl\.off \} : \{\}\)/);
  const glue = fs.readFileSync(path.join(STATIC, 'desktopControl.js'), 'utf8');
  assert.match(glue, /desktopControlState\(window\.starfireDesktopLink, window\.starfireDesktopUnlinked === true\)/);
  assert.match(send, /obj\.control_request\) \{\s*startControlStep\(obj\.control_request\)/);
  const page = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const order = ['browserOpen.js', 'desktopControlLogic.js', 'desktopControl.js', 'chatSend.js'].map((name) => page.indexOf(`<script src="${name}"></script>`));
  assert.ok(order.every((place) => place > 0) && order[0] < order[1] && order[1] < order[2] && order[2] < order[3], order.join());
});

test('the page glue talks to the step routes only and its names do not clash with any other page script', () => {
  const glue = fs.readFileSync(path.join(STATIC, 'desktopControl.js'), 'utf8');
  const logic = fs.readFileSync(path.join(STATIC, 'desktopControlLogic.js'), 'utf8');
  assert.match(glue, /\/api\/control-steps\//);
  assert.doesNotMatch(glue + logic, /screen-looks|browser-opens|lookAtScreen\(|openInBrowser\(|linkComputer|linkPair|linkUnpair|runComputer|computer_|innerHTML/);
  const topLevel = (source) => new Set([...source.matchAll(/^(?:async )?(?:function|const|let|var) (\w+)/gm)].map((match) => match[1]));
  const page = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const others = [...page.matchAll(/<script src="([\w.]+\.js)"><\/script>/g)].map((match) => match[1]).filter((name) => !name.startsWith('desktopControl'));
  assert.ok(others.includes('browserOpenLogic.js') && others.includes('chatSend.js'));
  const otherNames = new Set(others.flatMap((name) => [...topLevel(fs.readFileSync(path.join(STATIC, name), 'utf8'))]));
  const clashes = [...topLevel(glue), ...topLevel(logic)].filter((name) => otherNames.has(name));
  assert.deepEqual(clashes, []);
});

test('the CI checks the new page scripts', () => {
  const workflow = fs.readFileSync(path.join(__dirname, '..', '..', '.github', 'workflows', 'tests.yml'), 'utf8');
  assert.match(workflow, /node --check static\/desktopControlLogic\.js && node --check static\/desktopControl\.js/);
});
