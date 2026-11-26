'use strict';

const CONTROL_FAILURE_REASONS = new Set(['declined', 'timed_out', 'abandoned', 'busy', 'bad_request', 'unavailable', 'not_running', 'not_linked', 'failed']);
const CONTROL_ACTIONS = new Set(['apps', 'open', 'click', 'type', 'key', 'scroll']);
const CONTROL_MAX_DETAIL_CHARS = 300;
const CONTROL_MAX_RESULT_CHARS = 4000;
const CONTROL_MAX_SHOWN_CHARS = 60;
const CONTROL_MAX_ANSWER_SHOWN_CHARS = 120;
const CONTROL_CAPABILITY = 'screen.control';

function controlShown(value) {
  const text = typeof value === 'string' ? value : '';
  return text.length > CONTROL_MAX_SHOWN_CHARS ? `${text.slice(0, CONTROL_MAX_SHOWN_CHARS - 3)}...` : text;
}

function controlStepTitle(action, args) {
  if (action === 'apps') return 'list your open windows';
  if (action === 'open') return `open ${controlShown(args.app)}`;
  if (action === 'click') return typeof args.target === 'string' ? `click ${controlShown(args.target)}` : `click at ${args.x}, ${args.y}`;
  if (action === 'type') return `type "${controlShown(args.text)}"`;
  if (action === 'key') return `press ${controlShown(args.keys)}`;
  return `scroll ${controlShown(args.direction) || 'down'}`;
}

function controlAnswerShown(said) {
  const flat = typeof said === 'string' ? said.replace(/\s+/g, ' ').trim() : '';
  if (!flat) return 'nothing';
  return flat.length > CONTROL_MAX_ANSWER_SHOWN_CHARS ? `${flat.slice(0, CONTROL_MAX_ANSWER_SHOWN_CHARS - 3)}...` : flat;
}

function controlStepText(kind, action, args, said = '') {
  const title = controlStepTitle(action, args);
  if (kind === 'working') return `Asking Arty to ${title} on your PC...`;
  if (kind === 'waiting') return 'Waiting for you to allow it in Arty';
  return `Arty answered (${title}): ${controlAnswerShown(said)}`;
}

function controlFailureText(reason, action, args, detail) {
  const title = controlStepTitle(action, args);
  if (reason === 'declined') return `You said no in Arty, so I did not ${title}`;
  if (reason === 'timed_out') return `Nobody allowed it in Arty in time, so I did not ${title}`;
  if (reason === 'not_running') return `Arty Desktop is not running, so I could not ${title}`;
  if (reason === 'not_linked') return `starfire Desktop is not linked to Arty Desktop, so I could not ${title}`;
  if (reason === 'unavailable') return `Controlling your PC is turned off in Arty, so I could not ${title}`;
  if (reason === 'busy') return `Arty was busy with another step, so I did not ${title}`;
  if (reason === 'failed') return `Could not tell whether Arty did it (${title})${detail ? `: ${detail}` : ''}`;
  return `Could not ${title}: ${detail || 'it failed'}`;
}

function controlReason(reason) {
  return CONTROL_FAILURE_REASONS.has(reason) ? reason : 'failed';
}

async function desktopControlStatus(link) {
  if (!link || typeof link.linkStatus !== 'function' || typeof link.controlStep !== 'function') return { connected: false, available: false };
  try {
    const status = await link.linkStatus();
    const capabilities = Array.isArray(status.capabilities) ? status.capabilities : [];
    const available = capabilities.some((capability) => capability && capability.id === CONTROL_CAPABILITY && capability.available === true);
    return { connected: status.connected === true, available: status.connected === true && available };
  } catch (error) {
    return { connected: false, available: false };
  }
}

async function desktopControlState(link, unlinked) {
  if (!link) return { offered: false, off: unlinked === true ? 'not_linked' : '' };
  if (typeof link.controlStep !== 'function') return { offered: false, off: 'desktop_outdated' };
  const status = await desktopControlStatus(link);
  if (status.available) return { offered: true, off: '' };
  return { offered: false, off: status.connected ? 'screen_control_off' : 'not_running' };
}

async function unreadyControlFailure(link) {
  if (!link || typeof link.controlStep !== 'function') return { ok: false, reason: 'not_linked', error: '' };
  const status = await desktopControlStatus(link);
  if (!status.connected) return { ok: false, reason: 'not_running', error: '' };
  if (!status.available) return { ok: false, reason: 'unavailable', error: 'Controlling the PC is turned off in Arty Desktop.' };
  return null;
}

async function controlThroughArty(link, action, args, show) {
  const unready = await unreadyControlFailure(link);
  if (unready) return unready;
  show({ kind: 'waiting', text: controlStepText('waiting', action, args) });
  try {
    return await link.controlStep(action, args);
  } catch (error) {
    return { ok: false, reason: 'failed', error: 'starfire Desktop could not reach Arty Desktop.' };
  }
}

function isControlClaim(claimed) {
  return Boolean(claimed) && typeof claimed === 'object' && CONTROL_ACTIONS.has(claimed.action)
    && Boolean(claimed.arguments) && typeof claimed.arguments === 'object' && !Array.isArray(claimed.arguments);
}

function controlText(value, limit) {
  return typeof value === 'string' ? value.slice(0, limit) : '';
}

async function runControlStep({ request, link, api, show }) {
  const claimed = await api.claim(request.request_id);
  if (!isControlClaim(claimed)) return;
  const { action, arguments: args } = claimed;
  show({ kind: 'working', text: controlStepText('working', action, args) });
  const result = await controlThroughArty(link, action, args, show);
  if (result.ok === true) {
    const said = controlText(result.result, CONTROL_MAX_RESULT_CHARS);
    if (await api.answer(request.request_id, { ok: true, result: said })) {
      show({ kind: 'done', text: controlStepText('done', action, args, said) });
      return;
    }
    show({ kind: 'failed', text: `Arty may have done it (${controlStepTitle(action, args)}), but I could not tell starfire, so the reply will not know` });
    return;
  }
  const reason = controlReason(result.reason);
  const detail = controlText(result.error, CONTROL_MAX_DETAIL_CHARS);
  await api.answer(request.request_id, { ok: false, reason, detail });
  show({ kind: 'failed', text: controlFailureText(reason, action, args, detail) });
}

function createControlView({ document, output, scrollToEnd }) {
  const lines = new Map();

  function lineFor(id) {
    if (lines.has(id)) return lines.get(id);
    const line = document.createElement('div');
    line.className = 'line info desktop-control';
    const text = document.createElement('span');
    line.append(text);
    output.append(line);
    lines.set(id, line);
    return line;
  }

  function show(id, step) {
    const line = lineFor(id);
    line.children[0].textContent = step.text;
    line.className = step.kind === 'failed' ? 'line err desktop-control' : 'line info desktop-control';
    scrollToEnd();
  }

  return { show };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CONTROL_FAILURE_REASONS, controlStepText, controlFailureText, runControlStep, desktopControlState, createControlView };
}
