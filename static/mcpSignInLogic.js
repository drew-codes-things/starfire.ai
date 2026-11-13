'use strict';

const MCP_SIGN_IN_PARAM = 'mcp_sign_in';
const MCP_SIGN_IN_PARAMS = [MCP_SIGN_IN_PARAM, 'server', 'reason'];
const MCP_SIGN_IN_MARKER = 'sf_mcp_sign_in_started';

function toolCountText(count) {
  return `${count} tool${count === 1 ? '' : 's'}`;
}

function mcpServerState(server) {
  if (server.needs_sign_in) {
    return { kind: 'signed-out', text: 'Needs sign-in again' + (server.problem ? `: ${server.problem}` : '') };
  }
  if (server.connected) return { kind: 'connected', text: `Connected with ${toolCountText(server.tool_count || 0)}` };
  if (server.problem) return { kind: 'failed', text: `Failed: ${server.problem}` };
  return { kind: 'idle', text: `${toolCountText(server.tool_count || 0)}, not running` };
}

function signInResultFromSearch(search, startedHere) {
  if (!startedHere) return null;
  const params = new URLSearchParams(search || '');
  const outcome = params.get(MCP_SIGN_IN_PARAM);
  if (outcome !== 'connected' && outcome !== 'failed') return null;
  return { connected: outcome === 'connected', serverId: params.get('server') || '', reason: params.get('reason') || '' };
}

function listedServer(result, servers) {
  if (!result || !result.serverId || !Array.isArray(servers)) return null;
  return servers.find(server => server && server.id === result.serverId) || null;
}

function signInHostNotice(answer) {
  const signInHost = (answer && answer.sign_in_host) || '';
  const serverHost = (answer && answer.server_host) || '';
  if (!signInHost || signInHost === serverHost) return '';
  return `This server signs in through ${signInHost}. Continue only if you trust that site with this sign-in.`;
}

function signInResultMessage(result, serverName) {
  if (!result) return '';
  const name = serverName || 'The MCP server';
  if (result.connected) return `${name} is signed in and connected`;
  return `Sign-in failed: ${result.reason || 'no reason given'}`;
}

function searchWithoutSignInResult(search) {
  const params = new URLSearchParams(search || '');
  MCP_SIGN_IN_PARAMS.forEach(name => params.delete(name));
  const rest = params.toString();
  return rest ? `?${rest}` : '';
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    MCP_SIGN_IN_MARKER, mcpServerState, signInResultFromSearch, listedServer, signInHostNotice, signInResultMessage,
    searchWithoutSignInResult,
  };
}
