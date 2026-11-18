const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { composerScript, MAX_QUESTION_CHARS, cleanQuestion } = require('../src/quickask');

function fakePage({ input = true, disabled = false, send = true, clear = true } = {}) {
  const log = [];
  const element = (name, extra = {}) => ({
    ...extra,
    click() { log.push(`click:${name}`); },
    focus() { log.push(`focus:${name}`); },
    dispatchEvent(event) { log.push(`event:${name}:${event.type}`); return true; },
  });
  const elements = {};
  if (clear) elements.clearBtn = element('clearBtn');
  if (input) elements.msgInput = element('msgInput', { value: '', disabled });
  if (send) elements.sendBtn = element('sendBtn', { disabled: false });
  const document = { getElementById: (id) => elements[id] || null };
  class Event { constructor(type) { this.type = type; } }
  return { document, elements, log, Event };
}

function run(question, page) {
  const context = vm.createContext({ document: page.document, Event: page.Event });
  return vm.runInContext(composerScript(question), context);
}

test('the script starts a new chat, fills the box and presses send', () => {
  const page = fakePage();
  const result = run('What is the capital of France?', page);
  assert.equal(result, 'sent');
  assert.equal(page.elements.msgInput.value, 'What is the capital of France?');
  assert.deepEqual(page.log, ['click:clearBtn', 'event:msgInput:input', 'click:sendBtn']);
});

test('quotes, newlines and script-looking text are carried as plain data', () => {
  const page = fakePage();
  const question = '"; document.body.remove(); //\n`${1+1}` </script><img src=x onerror=alert(1)>';
  assert.equal(run(question, page), 'sent');
  assert.equal(page.elements.msgInput.value, question);
});

test('a page that is not ready is reported, not clicked', () => {
  assert.equal(run('hi', fakePage({ input: false })), 'not-ready');
  assert.equal(run('hi', fakePage({ disabled: true })), 'not-ready');
  assert.equal(run('hi', fakePage({ send: false })), 'not-ready');
  const page = fakePage({ disabled: true });
  run('hi', page);
  assert.equal(page.log.includes('click:sendBtn'), false);
});

test('a missing new chat button does not stop the question', () => {
  const page = fakePage({ clear: false });
  assert.equal(run('hi', page), 'sent');
});

test('questions are trimmed and limited', () => {
  assert.equal(cleanQuestion('  hello \n'), 'hello');
  assert.equal(cleanQuestion('   '), '');
  assert.equal(cleanQuestion(undefined), '');
  assert.equal(cleanQuestion(7), '');
  assert.equal(cleanQuestion('x'.repeat(MAX_QUESTION_CHARS + 50)).length, MAX_QUESTION_CHARS);
});
