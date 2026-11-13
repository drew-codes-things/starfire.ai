'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, '..', '..', 'static');
const SESSION = '0123456789abcdef';
const QUESTION_PAUSE_MS = 1000;

class FakeElement {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.parent = null;
    this.style = {};
    this.scrollHeight = 0;
    const element = this;
    this.classList = {
      add(name) { element.className = `${element.className} ${name}`.trim(); },
      remove(name) { element.className = element.className.split(' ').filter((part) => part !== name).join(' '); },
      toggle(name, on) { if (on) this.add(name); else this.remove(name); },
      contains(name) { return element.className.split(' ').includes(name); },
    };
  }

  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  removeEventListener(type) { delete this.listeners[type]; }
  adopt(children) {
    for (const child of children) if (child instanceof FakeElement) child.parent = this;
    return children;
  }
  append(...children) { this.children.push(...this.adopt(children)); }
  replaceChildren(...children) { this.children = this.adopt(children); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
  closest() { return null; }
  get innerHTML() { throw new Error('event text must not go through innerHTML'); }
  set innerHTML(value) { throw new Error('event text must not go through innerHTML'); }
}

function allElements(element) {
  return element.children.filter((child) => child instanceof FakeElement).flatMap((child) => [child, ...allElements(child)]);
}

function allText(element) {
  return [element.textContent, ...element.children.map((child) => (child instanceof FakeElement ? allText(child) : String(child)))].join(' ');
}

function byClass(root, name) {
  return allElements(root).filter((element) => element.className.split(' ').includes(name));
}

function button(root, label) {
  return allElements(root).find((element) => element.tag === 'button' && allText(element).trim() === label);
}

function settle() {
  return new Promise((resolve) => { setImmediate(resolve); });
}

function reply(status, body) {
  return { status, ok: status < 400, json: async () => body };
}

function openPage(routes) {
  const root = new FakeElement('div');
  const card = new FakeElement('main');
  const calls = [];
  const document = {
    createElement: (tag) => new FakeElement(tag),
    getElementById: () => root,
    querySelector: () => card,
  };
  const window = {
    location: { search: `?session=${SESSION}`, href: `https://starfire.example/arty-cli.html?session=${SESSION}`, pathname: '/arty-cli.html' },
    history: { pushState() {} },
    addEventListener() {},
    matchMedia: () => ({ matches: true }),
  };
  const fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : undefined });
    const route = routes.find(([pattern, method]) => url.includes(pattern) && (init.method || 'GET') === method);
    return route ? route[2]() : new Promise(() => {});
  };
  const timers = new Set();
  const later = (callback, milliseconds) => {
    const timer = setTimeout(() => { timers.delete(timer); callback(); }, milliseconds);
    timers.add(timer);
    return timer;
  };
  const context = vm.createContext({ document, window, fetch, Node: FakeElement, URL, URLSearchParams, Date, setTimeout: later, clearTimeout, console });
  for (const script of ['artyPage.js', 'answerGuard.js', 'artyCliLogic.js', 'artyCli.js']) {
    vm.runInContext(fs.readFileSync(path.join(STATIC, script), 'utf8'), context, { filename: script });
  }
  const close = () => timers.forEach((timer) => clearTimeout(timer));
  return { root, card, calls, close };
}

function sequence(...answers) {
  return () => (answers.length ? answers.shift()() : new Promise(() => {}));
}

const SESSION_INFO = { id: SESSION, name: 'VivaDrew', folder: 'greeter', model: 'arty/qwen3:14b', mode: 'default', waiting_for: [] };

test('a session reads like a chat and untrusted event text never becomes markup', async (t) => {
  const events = [
    { seq: 1, type: 'user', text: 'fix it <script>alert(1)</script>' },
    { seq: 2, type: 'tool_call', tool: 'run_command', text: JSON.stringify({ command: 'pytest -q' }) },
    { seq: 3, type: 'tool_result', tool: 'run_command', text: '<b>3 passed</b>' },
    { seq: 4, type: 'tool_call', tool: 'mcp__evil__<img src=x onerror=alert(1)>', text: '{}' },
    { seq: 5, type: 'assistant', text: '**Done** <img src=x onerror=alert(1)>\n[open](javascript:alert(1)) [docs](https://example.com)' },
    { seq: 6, type: 'permission', request_id: 'r1', text: 'Edit app.py?\n+<svg onload=alert(1)>' },
  ];
  const { root, card, close } = openPage([
    ['/sessions/' + SESSION + '/events', 'GET', sequence(() => reply(200, { events, next: 6, ended: false, missed: false, session: SESSION_INFO }))],
    ['/api/arty-cli/sessions', 'GET', () => reply(200, { sessions: [SESSION_INFO] })],
  ]);
  t.after(close);
  for (let round = 0; round < 5; round += 1) await settle();
  const text = allText(root);
  assert.match(text, /fix it <script>alert\(1\)<\/script>/);
  assert.match(text, /Ran a command, used <img src=x onerror=alert\(1\)> from evil/);
  assert.match(text, /<b>3 passed<\/b>/);
  assert.match(text, /<img src=x onerror=alert\(1\)>/);
  assert.match(text, /\+<svg onload=alert\(1\)>/);
  assert.match(text, /Waiting for you/);
  const tags = new Set(allElements(root).map((element) => element.tag));
  for (const tag of ['script', 'svg', 'b']) assert.equal(tags.has(tag), false, `a ${tag} element was made from event text`);
  const pictures = allElements(root).filter((element) => element.tag === 'img').map((image) => image.attributes.src);
  assert.deepEqual([...new Set(pictures)], ['assets/logo.svg']);
  const links = allElements(root).filter((element) => element.tag === 'a');
  assert.deepEqual(links.map((link) => link.attributes.href), ['https://example.com']);
  assert.ok(allElements(root).some((element) => element.tag === 'strong' && allText(element).includes('Done')));
  assert.equal(card.className.includes('arty-cli-live'), true);
  assert.match(text, /VivaDrew/);
  assert.match(text, /greeter · arty\/qwen3:14b · default mode/);
});

test('an answer is sent once and its card goes, and after the end the composer stays switched off with the reason', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 1_000_000 });
  let finish;
  const events = [{ seq: 1, type: 'permission', request_id: 'r1', text: 'Run command: pytest' }];
  const { root, calls, close } = openPage([
    ['/sessions/' + SESSION + '/events', 'GET', sequence(
      () => reply(200, { events, next: 1, ended: false, missed: false, session: SESSION_INFO }),
      () => new Promise((resolve) => { finish = () => resolve(reply(200, { events: [{ seq: 2, type: 'end' }], next: 2, ended: true, session: SESSION_INFO })); }))],
    ['/answers', 'POST', () => reply(200, { ok: true })],
    ['/api/arty-cli/sessions', 'GET', () => reply(200, { sessions: [SESSION_INFO] })],
  ]);
  t.after(close);
  for (let round = 0; round < 5; round += 1) await settle();
  const allow = button(root, 'Allow');
  assert.ok(allow, 'the waiting request has an Allow button');
  assert.equal(button(root, 'Stop').hidden, false);
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  allow.listeners.click();
  allow.listeners.click();
  for (let round = 0; round < 5; round += 1) await settle();
  assert.deepEqual(calls.filter((call) => call.url.endsWith('/answers')).map((call) => call.body), [{ request_id: 'r1', allow: true }]);
  assert.equal(byClass(root, 'arty-cli-permission').filter((element) => !element.hidden).length, 0);
  finish();
  for (let round = 0; round < 5; round += 1) await settle();
  const box = allElements(root).find((element) => element.tag === 'textarea');
  assert.equal(box.disabled, true);
  assert.equal(button(root, 'Send').disabled, true);
  assert.equal(button(root, 'Stop').hidden, true);
  assert.match(allText(root), /can't take messages/);
  assert.match(allText(root), /Session ended/);
});

test('a new request ignores Allow and Deny until it has been on screen for a second, and says it is new', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 1_000_000 });
  const events = [{ seq: 1, type: 'permission', request_id: 'r1', text: 'Run command: rm -rf build' }];
  const { root, calls, close } = openPage([
    ['/sessions/' + SESSION + '/events', 'GET', sequence(() => reply(200, { events, next: 1, ended: false, missed: false, session: SESSION_INFO }))],
    ['/answers', 'POST', () => reply(200, { ok: true })],
    ['/api/arty-cli/sessions', 'GET', () => reply(200, { sessions: [SESSION_INFO] })],
  ]);
  t.after(close);
  for (let round = 0; round < 5; round += 1) await settle();
  const answers = () => calls.filter((call) => call.url.endsWith('/answers')).map((call) => call.body);
  const allow = button(root, 'Allow');
  assert.match(allText(byClass(root, 'arty-cli-permission')[0]), /New question/);
  assert.equal(allow.getAttribute('aria-disabled'), 'true');
  allow.listeners.click();
  button(root, 'Deny').listeners.click();
  t.mock.timers.tick(QUESTION_PAUSE_MS - 1);
  allow.listeners.click();
  for (let round = 0; round < 5; round += 1) await settle();
  assert.deepEqual(answers(), []);
  t.mock.timers.tick(1);
  allow.listeners.click();
  for (let round = 0; round < 5; round += 1) await settle();
  assert.deepEqual(answers(), [{ request_id: 'r1', allow: true }]);
});

test('a shared terminal shows its title, the earlier conversation and the compacted summary without markup', async (t) => {
  const info = { ...SESSION_INFO, title: 'greeter 2' };
  const events = [
    { seq: 1, type: 'compacted', text: 'We fixed **greet()** <img src=x onerror=alert(1)>', history: true },
    { seq: 2, type: 'user', text: 'now the docs', history: true },
    { seq: 3, type: 'assistant', text: 'Docs done.', history: true },
    { seq: 4, type: 'user', text: 'ship it' },
  ];
  const { root, close } = openPage([
    ['/sessions/' + SESSION + '/events', 'GET', sequence(() => reply(200, { events, next: 4, ended: false, missed: false, session: info }))],
    ['/api/arty-cli/sessions', 'GET', () => reply(200, { sessions: [info] })],
  ]);
  t.after(close);
  for (let round = 0; round < 5; round += 1) await settle();
  const text = allText(root);
  assert.match(text, /greeter 2/);
  assert.match(text, /Conversation compacted/);
  assert.match(text, /We fixed/);
  assert.match(text, /now the docs/);
  assert.match(text, /Docs done\./);
  assert.match(text, /Sharing started here/);
  assert.equal(byClass(root, 'arty-cli-compacted').length, 1);
  const pictures = allElements(root).filter((element) => element.tag === 'img').map((image) => image.attributes.src);
  assert.deepEqual([...new Set(pictures)], ['assets/logo.svg']);
});

test('the message box grows with its text up to a limit and shrinks back after sending', async (t) => {
  const { root, close } = openPage([
    ['/sessions/' + SESSION + '/events', 'GET', sequence(() => reply(200, { events: [], next: 0, ended: false, missed: false, session: SESSION_INFO }))],
    ['/messages', 'POST', () => reply(200, { ok: true })],
    ['/api/arty-cli/sessions', 'GET', () => reply(200, { sessions: [SESSION_INFO] })],
  ]);
  t.after(close);
  for (let round = 0; round < 5; round += 1) await settle();
  const box = allElements(root).find((element) => element.tag === 'textarea');
  assert.ok(box.listeners.input, 'the message box listens for typing');
  box.value = 'one\ntwo\nthree';
  box.scrollHeight = 96;
  box.listeners.input();
  assert.equal(box.style.height, '96px');
  box.scrollHeight = 900;
  box.listeners.input();
  assert.equal(box.style.height, '200px');
  const form = allElements(root).find((element) => element.tag === 'form');
  form.listeners.submit({ preventDefault() {} });
  for (let round = 0; round < 5; round += 1) await settle();
  assert.equal(box.value, '');
  assert.equal(box.style.height, '');
});
