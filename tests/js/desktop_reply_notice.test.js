'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const STATIC = path.join(__dirname, '..', '..', 'static');
const { tellDesktopTheReplyIsReady } = require(path.join(STATIC, 'desktopReplyNotice.js'));

test('a finished reply tells Arty Desktop through its reply call, with nothing else passed', () => {
  const told = [];
  tellDesktopTheReplyIsReady({ starfireDesktopReplies: { ready: (...args) => told.push(args) } });
  assert.deepEqual(told, [[]]);
});

test('outside Desktop, or with no reply call offered, nothing happens', () => {
  for (const win of [null, undefined, {}, { starfireDesktopReplies: null }, { starfireDesktopReplies: { ready: 'no' } }]) {
    assert.doesNotThrow(() => tellDesktopTheReplyIsReady(win));
  }
});

test('the chat tells Desktop only after a reply with text came through without an error', () => {
  const source = fs.readFileSync(path.join(STATIC, 'chatSend.js'), 'utf8');
  const told = source.indexOf('tellDesktopTheReplyIsReady(window)');
  assert.equal(source.split('tellDesktopTheReplyIsReady(window)').length, 2);
  assert.ok(told > source.indexOf('if (streamError) throw'));
  assert.ok(told < source.indexOf('} catch (e) {', told));
  assert.match(source.slice(source.lastIndexOf('\n', told - 1), told), /if \(fullText\) /);
  const page = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  assert.ok(page.indexOf('desktopReplyNotice.js') > 0 && page.indexOf('desktopReplyNotice.js') < page.indexOf('chatSend.js'));
});
