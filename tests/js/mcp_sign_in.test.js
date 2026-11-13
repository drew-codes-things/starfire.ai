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
  assert.deepEqual(logic.signInResultFromSearch('?mcp_sign_in=connected&server=abc123', true),
    { connected: true, serverId: 'abc123', reason: '' });
  assert.deepEqual(logic.signInResultFromSearch('?mcp_sign_in=failed&reason=this+sign-in+took+too+long', true),
    { connected: false, serverId: '', reason: 'this sign-in took too long' });
  assert.equal(logic.signInResultFromSearch('?mcp_sign_in=<script>', true), null);
  assert.equal(logic.signInResultFromSearch('', true), null);
  assert.equal(logic.signInResultFromSearch('?chat=1', true), null);
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

test('a result link is ignored unless this tab started a sign-in', () => {
  assert.equal(logic.signInResultFromSearch('?mcp_sign_in=connected&server=0123456789ab', false), null);
  assert.equal(logic.signInResultFromSearch('?mcp_sign_in=failed&reason=log+in+again+at+https://x.example'), null);
  assert.equal(logic.signInResultMessage(null, ''), '');
  assert.equal(typeof logic.MCP_SIGN_IN_MARKER, 'string');
});

test('only a server that is really in the list is enabled after a sign-in', () => {
  const servers = [{ id: 'abc123', name: 'Notion' }];
  const result = logic.signInResultFromSearch('?mcp_sign_in=connected&server=abc123', true);
  assert.deepEqual(logic.listedServer(result, servers), { id: 'abc123', name: 'Notion' });
  const forged = logic.signInResultFromSearch('?mcp_sign_in=connected&server=0123456789ab', true);
  assert.equal(logic.listedServer(forged, servers), null);
  assert.equal(logic.listedServer(result, null), null);
  assert.equal(logic.listedServer(null, servers), null);
});

test('the admin is told when a server signs in through another host', () => {
  assert.equal(logic.signInHostNotice({ server_host: 'mcp.evil.example', sign_in_host: 'as.honest.example' }),
    'This server signs in through as.honest.example. Continue only if you trust that site with this sign-in.');
  assert.equal(logic.signInHostNotice({ server_host: 'mcp.notion.com', sign_in_host: 'mcp.notion.com' }), '');
  assert.equal(logic.signInHostNotice({}), '');
  assert.equal(logic.signInHostNotice(null), '');
});
