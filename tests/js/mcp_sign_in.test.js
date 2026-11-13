'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const logic = require(path.join(__dirname, '..', '..', 'static', 'mcpSignInLogic.js'));

test('a server is described by the state it is in, sign-in first', () => {
  assert.deepEqual(logic.mcpServerState({ connected: true, tool_count: 3 }),
    { kind: 'connected', text: 'Connected with 3 tools' });
  assert.deepEqual(logic.mcpServerState({ connected: true, tool_count: 1 }),
    { kind: 'connected', text: 'Connected with 1 tool' });
  assert.deepEqual(logic.mcpServerState({ needs_sign_in: true, connected: true, problem: 'renewing the sign-in was refused (400)' }),
    { kind: 'signed-out', text: 'Needs sign-in again: renewing the sign-in was refused (400)' });
  assert.deepEqual(logic.mcpServerState({ needs_sign_in: true }), { kind: 'signed-out', text: 'Needs sign-in again' });
  assert.deepEqual(logic.mcpServerState({ connected: false, problem: 'timed out' }),
    { kind: 'failed', text: 'Failed: timed out' });
  assert.deepEqual(logic.mcpServerState({ connected: false, tool_count: 0 }),
    { kind: 'idle', text: '0 tools, not running' });
});

test('the sign-in result is read from the address the service sent the browser back to', () => {
  assert.deepEqual(logic.signInResultFromSearch('?mcp_sign_in=connected&server=abc123'),
    { connected: true, serverId: 'abc123', reason: '' });
  assert.deepEqual(logic.signInResultFromSearch('?mcp_sign_in=failed&reason=this+sign-in+took+too+long'),
    { connected: false, serverId: '', reason: 'this sign-in took too long' });
  assert.equal(logic.signInResultFromSearch('?mcp_sign_in=<script>'), null);
  assert.equal(logic.signInResultFromSearch(''), null);
  assert.equal(logic.signInResultFromSearch('?chat=1'), null);
});

test('the toast names the server or the reason', () => {
  assert.equal(logic.signInResultMessage({ connected: true }, 'Notion'), 'Notion is signed in and connected');
  assert.equal(logic.signInResultMessage({ connected: true }, ''), 'The MCP server is signed in and connected');
  assert.equal(logic.signInResultMessage({ connected: false, reason: 'no code' }), 'Sign-in failed: no code');
  assert.equal(logic.signInResultMessage({ connected: false }), 'Sign-in failed: no reason given');
});

test('only the sign-in result is taken out of the address', () => {
  assert.equal(logic.searchWithoutSignInResult('?mcp_sign_in=connected&server=a1'), '');
  assert.equal(logic.searchWithoutSignInResult('?chat=7&mcp_sign_in=failed&reason=x'), '?chat=7');
  assert.equal(logic.searchWithoutSignInResult(''), '');
});
