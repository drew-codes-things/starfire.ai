const test = require('node:test');
const assert = require('node:assert/strict');
const { isReplyFinished, shouldNotify, replyNotification } = require('../src/notify');

const ORIGIN = 'https://starfire.example.com';

test('a finished chat stream on the starfire origin is a finished reply', () => {
  assert.equal(isReplyFinished({ method: 'POST', url: `${ORIGIN}/api/chat_stream`, statusCode: 200 }, ORIGIN), true);
  assert.equal(isReplyFinished({ method: 'POST', url: `${ORIGIN}/api/chat_stream?x=1`, statusCode: 200 }, ORIGIN), true);
});

test('other requests, failures and other origins are not replies', () => {
  const cases = [
    { method: 'GET', url: `${ORIGIN}/api/chat_stream`, statusCode: 200 },
    { method: 'POST', url: `${ORIGIN}/api/chat_stream`, statusCode: 500 },
    { method: 'POST', url: `${ORIGIN}/api/chat_stream`, statusCode: 0 },
    { method: 'POST', url: `${ORIGIN}/api/other`, statusCode: 200 },
    { method: 'POST', url: `${ORIGIN}/api/chat_stream/extra`, statusCode: 200 },
    { method: 'POST', url: 'https://evil.com/api/chat_stream', statusCode: 200 },
    { method: 'POST', url: `${ORIGIN}.evil.com/api/chat_stream`, statusCode: 200 },
    { method: 'POST', url: 'garbage', statusCode: 200 },
    {},
  ];
  for (const details of cases) {
    assert.equal(isReplyFinished(details, ORIGIN), false, JSON.stringify(details));
  }
});

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
