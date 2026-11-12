'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const sideChat = require(path.join(__dirname, '..', '..', 'static', 'sideChat.js'));

const MAIN_THREAD = [
  { role: 'user', content: 'Plan a trip to Rome' },
  { role: 'assistant', content: 'Day one: the Colosseum.' },
];

test('/side and /btw open a side chat and keep the question', () => {
  assert.deepEqual(sideChat.parseSideChatCommand('/side what is a forum?'), { question: 'what is a forum?' });
  assert.deepEqual(sideChat.parseSideChatCommand('/btw  how far is it?  '), { question: 'how far is it?' });
  assert.deepEqual(sideChat.parseSideChatCommand('/BTW quick one'), { question: 'quick one' });
});

test('a bare command opens an empty side chat', () => {
  assert.deepEqual(sideChat.parseSideChatCommand('/side'), { question: '' });
  assert.deepEqual(sideChat.parseSideChatCommand('  /btw  '), { question: '' });
});

test('a multi-line question after the command is kept whole', () => {
  assert.deepEqual(sideChat.parseSideChatCommand('/btw first line\nsecond line'), { question: 'first line\nsecond line' });
});

test('other text and look-alike commands are ordinary messages', () => {
  for (const text of ['/sidebar open', '/btwx', 'side note', 'tell me /side', '/summarise', '', null, undefined]) {
    assert.equal(sideChat.parseSideChatCommand(text), null, String(text));
  }
});

test('the prompt popup offers the side chat commands that match what was typed', () => {
  assert.deepEqual(sideChat.sideChatCommandsMatching(''), ['side', 'btw']);
  assert.deepEqual(sideChat.sideChatCommandsMatching('si'), ['side']);
  assert.deepEqual(sideChat.sideChatCommandsMatching('B'), ['btw']);
  assert.deepEqual(sideChat.sideChatCommandsMatching('summ'), []);
});

test('a side chat sends the main chat so far, then its own turns', () => {
  const state = sideChat.createSideChat(MAIN_THREAD, 'chat-1');
  const sideTurns = [{ role: 'user', content: 'Is the Colosseum open on Mondays?' }];
  assert.deepEqual(sideChat.sideChatContext(state, sideTurns), [...MAIN_THREAD, ...sideTurns]);
});

test('without a side chat the messages are sent as they are', () => {
  const turns = [{ role: 'user', content: 'hello' }];
  assert.equal(sideChat.sideChatContext(null, turns), turns);
});

test('the main chat is kept apart from the side chat and comes back unchanged', () => {
  const main = MAIN_THREAD.map(message => ({ ...message }));
  const state = sideChat.createSideChat(main, 'chat-1');
  main.push({ role: 'user', content: 'added after opening' });
  assert.equal(state.mainSessionId, 'chat-1');
  assert.deepEqual(state.mainMessages, MAIN_THREAD);
  assert.notEqual(state.mainMessages, main);
});

test('a side chat opened from a new, unsaved chat has no session to return to', () => {
  assert.equal(sideChat.createSideChat([], undefined).mainSessionId, null);
});
