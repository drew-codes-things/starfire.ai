'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const logic = require(path.join(__dirname, '..', '..', 'static', 'artyCliLogic.js'));

test('a session line names the computer, folder, model and mode', () => {
  assert.equal(logic.sessionLine({ name: 'VivaDrew', folder: 'arty-cli', model: 'arty/qwen3:14b', mode: 'plan' }),
    'VivaDrew · arty-cli · arty/qwen3:14b · plan mode');
  assert.equal(logic.sessionLine({}), 'A computer · default mode');
});

test('each event becomes a chat line, a tool line or a note', () => {
  assert.deepEqual(logic.eventView({ type: 'user', text: 'fix it' }), { kind: 'user', who: 'On the computer', text: 'fix it' });
  assert.deepEqual(logic.eventView({ type: 'remote_message', text: 'run tests', by: 'Phone' }),
    { kind: 'user', who: 'Phone', text: 'run tests' });
  assert.deepEqual(logic.eventView({ type: 'assistant', text: 'Done.' }), { kind: 'ai', who: 'Arty', text: 'Done.' });
  assert.deepEqual(logic.eventView({ type: 'tool_call', tool: 'bash', text: 'pytest -q' }),
    { kind: 'tool', who: 'bash', text: 'pytest -q' });
  assert.deepEqual(logic.eventView({ type: 'tool_result', tool: 'bash', text: '3 passed' }),
    { kind: 'tool', who: 'bash result', text: '3 passed' });
  assert.deepEqual(logic.eventView({ type: 'permission_answered', request_id: 'r1', allow: false, by: 'the computer' }),
    { kind: 'note', who: '', text: 'the computer said no.' });
  assert.deepEqual(logic.eventView({ type: 'end' }), { kind: 'note', who: '', text: 'The session ended on the computer.' });
  assert.equal(logic.eventView({ type: 'permission', request_id: 'r1' }), null);
  assert.equal(logic.eventView({ type: 'something new' }), null);
});

test('a waiting permission request is described with its tool and what it wants to do', () => {
  assert.equal(logic.permissionText({ tool: 'bash', text: 'rm -rf build' }), 'Arty wants to use bash: rm -rf build');
  assert.equal(logic.permissionText({}), 'Arty wants to use a tool');
});

test('requests still open are the waiting ones minus those answered since', () => {
  const waiting = [{ request_id: 'r1' }, { request_id: 'r2' }];
  const events = [{ type: 'permission', request_id: 'r3', tool: 'edit', text: 'a.py' },
    { type: 'permission_answered', request_id: 'r1', allow: true, by: 'Phone' }];
  assert.deepEqual(logic.openRequests(waiting, events).map((request) => request.request_id), ['r2', 'r3']);
});

test('only real session ids are used in addresses', () => {
  assert.equal(logic.isSessionId('0123456789abcdef'), true);
  assert.equal(logic.isSessionId('../devices'), false);
  assert.equal(logic.isSessionId('0123456789ABCDEF'), false);
});

test('after the first read every poll waits, even before the session has any events', () => {
  assert.deepEqual(logic.FIRST_POLL, { after: 0, wait: 0 });
  assert.deepEqual(logic.nextPoll(logic.FIRST_POLL, { next: 0 }, 20), { after: 0, wait: 20 });
  assert.deepEqual(logic.nextPoll({ after: 4, wait: 20 }, { next: 7 }, 20), { after: 7, wait: 20 });
  assert.deepEqual(logic.nextPoll({ after: 4, wait: 20 }, {}, 20), { after: 4, wait: 20 });
});
