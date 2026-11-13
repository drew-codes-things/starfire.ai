'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.join(__dirname, '..', '..', 'static');
const QUESTION_PAUSE_MS = 1000;

class FakeElement {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.attributes = {};
    this.textContent = '';
    this.parent = null;
    this.classes = new Set();
    const element = this;
    this.classList = {
      add(name) { element.classes.add(name); },
      remove(name) { element.classes.delete(name); },
      contains(name) { return element.classes.has(name); },
    };
  }

  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  append(...children) {
    for (const child of children) child.parent = this;
    this.children.push(...children);
  }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
}

function shownText(element) {
  return [element.textContent, ...element.children.map(shownText)].join(' ');
}

function loadGuard(t) {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1_000_000 });
  global.document = { createElement: (tag) => new FakeElement(tag) };
  delete global.IntersectionObserver;
  const file = path.join(STATIC, 'answerGuard.js');
  delete require.cache[file];
  return require(file);
}

test('a question is answerable one second after it is seen, with a flash until then', (t) => {
  const { ANSWER_PAUSE_MS, answerGuard, flashNewQuestion } = loadGuard(t);
  assert.equal(ANSWER_PAUSE_MS, QUESTION_PAUSE_MS);
  const card = new FakeElement('div');
  const approve = new FakeElement('button');
  card.append(approve);
  const guard = answerGuard();
  flashNewQuestion(card, [approve], guard);
  assert.equal(guard.isReady(), false);
  assert.match(shownText(card), /New question/);
  assert.equal(card.classList.contains('answer-waiting'), true);
  assert.equal(approve.getAttribute('aria-disabled'), 'true');
  t.mock.timers.tick(QUESTION_PAUSE_MS - 1);
  assert.equal(guard.isReady(), false);
  t.mock.timers.tick(1);
  assert.equal(guard.isReady(), true);
  assert.doesNotMatch(shownText(card), /New question/);
  assert.equal(card.classList.contains('answer-waiting'), false);
  assert.equal(approve.getAttribute('aria-disabled'), null);
});

test('a question that is not on screen yet does not start its second', (t) => {
  const { answerGuard, flashNewQuestion } = loadGuard(t);
  const observers = [];
  global.IntersectionObserver = class {
    constructor(callback) { this.callback = callback; observers.push(this); }
    observe(element) { this.element = element; }
    disconnect() { this.disconnected = true; }
  };
  t.after(() => { delete global.IntersectionObserver; });
  const card = new FakeElement('div');
  const guard = answerGuard();
  flashNewQuestion(card, [], guard);
  t.mock.timers.tick(10 * QUESTION_PAUSE_MS);
  assert.equal(guard.isReady(), false);
  observers[0].callback([{ target: card, isIntersecting: false }]);
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  assert.equal(guard.isReady(), false);
  observers[0].callback([{ target: card, isIntersecting: true }]);
  assert.equal(observers[0].disconnected, true);
  t.mock.timers.tick(QUESTION_PAUSE_MS);
  assert.equal(guard.isReady(), true);
});

function functionBody(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} exists`);
  const next = source.indexOf('\nfunction ', start + 1);
  return source.slice(start, next < 0 ? undefined : next);
}

test('the chat approval cards for edits and shell commands hold their answers the same way', () => {
  const app = fs.readFileSync(path.join(STATIC, 'app.js'), 'utf8');
  for (const name of ['renderDiffCard', 'renderShellApprovalCard']) {
    const body = functionBody(app, name);
    assert.match(body, /const guard = answerGuard\(\);/, name);
    assert.match(body, /flashNewQuestion\(card, \[approveBtn, rejectBtn\], guard\);/, name);
    const handlers = body.split(/(?:approveBtn\.onclick|rejectBtn\.onclick|const decide) = async/).slice(1);
    assert.ok(handlers.length >= 1, name);
    for (const handler of handlers) assert.match(handler.trimStart(), /^\([^)]*\) => \{\s*if \(!guard\.isReady\(\)\) return;/, name);
  }
  const page = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  assert.ok(page.indexOf('src="answerGuard.js"') >= 0 && page.indexOf('src="answerGuard.js"') < page.indexOf('src="app.js"'));
  const cliPage = fs.readFileSync(path.join(STATIC, 'arty-cli.html'), 'utf8');
  assert.ok(cliPage.indexOf('src="answerGuard.js"') >= 0 && cliPage.indexOf('src="answerGuard.js"') < cliPage.indexOf('src="artyCli.js"'));
});
