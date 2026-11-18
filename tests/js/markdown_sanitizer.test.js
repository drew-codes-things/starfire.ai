'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const STATIC = path.join(__dirname, '..', '..', 'static');
const TAGS_THAT_MUST_NOT_SURVIVE = ['style', 'form', 'input', 'button', 'meta', 'base', 'link'];
const ATTRIBUTES_THAT_MUST_NOT_SURVIVE = ['style', 'srcset', 'id', 'name', 'formaction'];

function loadChatMessagesWithRecordingPurifier() {
  const purifier = { hooks: [], calls: [] };
  purifier.addHook = (name, hook) => purifier.hooks.push({ name, hook });
  purifier.sanitize = (html, options) => {
    purifier.calls.push({ html, options });
    return html;
  };
  const marked = { parse: text => `<p>${text}</p>` };
  const window = { DOMPurify: purifier, marked, addEventListener() {} };
  const document = { addEventListener() {} };
  const context = vm.createContext({ window, document, URL, location: { origin: 'https://app.example' } });
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'uiLogic.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(STATIC, 'chatMessages.js'), 'utf8'), context);
  return { purifier, render: text => vm.runInContext(`markdownRender(${JSON.stringify(text)})`, context) };
}

test('markdown is sanitised with style, form and link tags and their risky attributes forbidden', () => {
  const { purifier, render } = loadChatMessagesWithRecordingPurifier();
  render('hello');
  assert.equal(purifier.calls.length, 1);
  const { options } = purifier.calls[0];
  for (const tag of TAGS_THAT_MUST_NOT_SURVIVE) assert.ok(options.FORBID_TAGS.includes(tag), tag);
  for (const name of ATTRIBUTES_THAT_MUST_NOT_SURVIVE) assert.ok(options.FORBID_ATTR.includes(name), name);
});

test('the class and off-site media guards are installed on the sanitiser when the page loads', () => {
  const { purifier } = loadChatMessagesWithRecordingPurifier();
  const byName = Object.fromEntries(purifier.hooks.map(({ name, hook }) => [name, hook]));
  assert.deepEqual(Object.keys(byName).sort(), ['afterSanitizeAttributes', 'uponSanitizeAttribute']);

  const kept = { attrName: 'class', attrValue: 'language-python', keepAttr: true };
  const dropped = { attrName: 'class', attrValue: 'evil-overlay', keepAttr: true };
  byName.uponSanitizeAttribute(null, kept);
  byName.uponSanitizeAttribute(null, dropped);
  assert.equal(kept.keepAttr, true);
  assert.equal(dropped.keepAttr, false);

  const attributes = { src: 'https://tracker.example/pixel.png', poster: '/api/files/a.png' };
  const media = {
    hasAttribute: name => name in attributes,
    getAttribute: name => attributes[name],
    removeAttribute: name => { delete attributes[name]; },
  };
  byName.afterSanitizeAttributes(media);
  assert.deepEqual(attributes, { poster: '/api/files/a.png' });
});
