'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const logic = require(path.join(__dirname, '..', '..', 'static', 'uiLogic.js'));

const NOW = 1_800_000_100;

test('an answering connection shows Available and when it was checked', () => {
  const badge = logic.connectionHealthBadge({ available: true, last_checked: NOW - 12, error_kind: '' }, NOW);
  assert.deepEqual(badge, { text: 'Available · checked 12s ago', cls: 'mcp-state-connected' });
});

test('a connection that stopped answering shows Unavailable and why', () => {
  const badge = logic.connectionHealthBadge({ available: false, last_checked: NOW - 3, error_kind: 'timeout' }, NOW);
  assert.deepEqual(badge, { text: 'Unavailable (timed out) · checked 3s ago', cls: 'mcp-state-failed' });
  assert.match(logic.connectionHealthBadge({ available: false, last_checked: NOW, error_kind: 'unauthorized' }, NOW).text,
    /key refused/);
});

test('a check longer ago is shown in minutes', () => {
  assert.equal(logic.connectionHealthBadge({ available: true, last_checked: NOW - 150 }, NOW).text,
    'Available · checked 2 min ago');
});

test('a connection not checked yet says so', () => {
  assert.deepEqual(logic.connectionHealthBadge(null, NOW), { text: 'not checked yet', cls: 'mcp-state-signed-out' });
});
