'use strict';

const SESSION_ID = /^[0-9a-f]{16}$/;
const UNNAMED_COMPUTER = 'A computer';
const DEFAULT_MODE = 'default';
const ON_THE_COMPUTER = 'On the computer';

function sessionLine(session) {
  const parts = [String(session?.name || UNNAMED_COMPUTER)];
  if (session?.folder) parts.push(String(session.folder));
  if (session?.model) parts.push(String(session.model));
  parts.push(`${session?.mode || DEFAULT_MODE} mode`);
  return parts.join(' · ');
}

function eventView(event) {
  const text = String(event?.text ?? '');
  switch (event?.type) {
    case 'user': return { kind: 'user', who: ON_THE_COMPUTER, text };
    case 'remote_message': return { kind: 'user', who: String(event.by || 'Someone'), text };
    case 'assistant': return { kind: 'ai', who: 'Arty', text };
    case 'tool_call': return { kind: 'tool', who: String(event.tool || 'tool'), text };
    case 'tool_result': return { kind: 'tool', who: `${event.tool || 'tool'} result`, text };
    case 'permission_answered':
      return { kind: 'note', who: '', text: `${event.by || ON_THE_COMPUTER} ${event.allow ? 'allowed it' : 'said no'}.` };
    case 'status': return text ? { kind: 'note', who: '', text } : null;
    case 'error': return { kind: 'note', who: '', text: `Problem: ${text}` };
    case 'end': return { kind: 'note', who: '', text: 'The session ended on the computer.' };
    default: return null;
  }
}

function permissionText(request) {
  const tool = request?.tool ? String(request.tool) : '';
  if (!tool) return 'Arty wants to use a tool';
  const detail = request?.text ? `: ${request.text}` : '';
  return `Arty wants to use ${tool}${detail}`;
}

function openRequests(waiting, events) {
  const open = new Map((waiting || []).map((request) => [request.request_id, request]));
  for (const event of events || []) {
    if (event.type === 'permission' && event.request_id) open.set(event.request_id, event);
    if (event.type === 'permission_answered') open.delete(event.request_id);
  }
  return [...open.values()];
}

function isSessionId(value) {
  return SESSION_ID.test(String(value ?? ''));
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { sessionLine, eventView, permissionText, openRequests, isSessionId };
}
