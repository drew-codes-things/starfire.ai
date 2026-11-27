const test = require('node:test');
const assert = require('node:assert/strict');
const { shouldNotify, replyNotification } = require('../src/notify');

test('notifications are shown only when enabled and the window is not being looked at', () => {
  assert.equal(shouldNotify({ enabled: true, windowVisible: false, windowFocused: false }), true);
  assert.equal(shouldNotify({ enabled: true, windowVisible: true, windowFocused: false }), true);
  assert.equal(shouldNotify({ enabled: true, windowVisible: true, windowFocused: true }), false);
  assert.equal(shouldNotify({ enabled: false, windowVisible: false, windowFocused: false }), false);
});

test('the notification says only that a reply is ready', () => {
  const note = replyNotification();
  assert.equal(note.title, 'starfire');
  assert.match(note.body, /reply/i);
  assert.equal(note.silent, false);
});
