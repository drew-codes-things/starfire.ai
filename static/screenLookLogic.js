'use strict';

const LOOK_FAILURE_REASONS = new Set(['declined', 'timed_out', 'not_running', 'not_linked', 'not_found', 'unavailable', 'cannot_see', 'off_limits', 'busy', 'ambiguous', 'changed', 'failed']);
const LOOK_MAX_DETAIL_CHARS = 300;
const LOOK_PICTURE_TYPES = new Set(['image/jpeg', 'image/png']);
const LOOK_BASE64 = /^[A-Za-z0-9+/]+={0,2}$/;
const LOOK_WHOLE_SCREEN = 'your screen';

function lookTarget(app) {
  return app || LOOK_WHOLE_SCREEN;
}

function describerNote(describedBy) {
  return describedBy ? ` (${describedBy} describes it to the chat model)` : '';
}

function stepText(kind, app, describedBy) {
  const target = lookTarget(app);
  if (kind === 'looking') return `Looking at ${target} on your PC...`;
  if (kind === 'waiting') return 'Waiting for you to allow it in Arty';
  return `Looked at ${target}${describerNote(describedBy)}`;
}

function failureText(reason, app, detail) {
  const target = lookTarget(app);
  if (reason === 'declined') return `You said no in Arty, so I did not look at ${target}`;
  if (reason === 'timed_out') return `Nobody allowed it in Arty in time, so I did not look at ${target}`;
  if (reason === 'not_running') return `Arty Desktop is not running, so I could not look at ${target}`;
  if (reason === 'not_linked') return `starfire Desktop is not linked to Arty Desktop, so I could not look at ${target}`;
  return `Could not look at ${target}: ${detail || 'it failed'}`;
}

function lookReason(reason) {
  return LOOK_FAILURE_REASONS.has(reason) ? reason : 'failed';
}

async function desktopScreenConnected(link) {
  if (!link || typeof link.linkStatus !== 'function') return false;
  try {
    return (await link.linkStatus()).connected === true;
  } catch (error) {
    return false;
  }
}

async function unreadyLinkFailure(link) {
  if (!link) return { ok: false, reason: 'not_linked', error: '' };
  if (!(await desktopScreenConnected(link))) return { ok: false, reason: 'not_running', error: '' };
  return null;
}

async function lookThroughArty(link, app, show) {
  const unready = await unreadyLinkFailure(link);
  if (unready) return unready;
  show({ kind: 'waiting', text: stepText('waiting', app) });
  try {
    return await link.lookAtScreen(app);
  } catch (error) {
    return { ok: false, reason: 'failed', error: 'starfire Desktop could not reach Arty Desktop.' };
  }
}

function plainDetail(value) {
  return typeof value === 'string' ? value.slice(0, LOOK_MAX_DETAIL_CHARS) : '';
}

async function runLook({ request, link, api, show }) {
  const claimed = await api.claim(request.request_id);
  if (!claimed) return;
  const app = claimed.app || '';
  show({ kind: 'looking', text: stepText('looking', app) });
  const result = await lookThroughArty(link, app, show);
  if (result.ok === true) {
    const picture = { image: result.image, mime: result.mime };
    const handed = await api.answer(request.request_id, { ok: true, ...picture, target: result.target, window: result.window, note: result.note });
    if (handed) {
      show({ kind: 'done', text: stepText('done', app, plainDetail(request.described_by)), picture });
      return;
    }
    show({ kind: 'failed', text: `The picture of ${lookTarget(app)} could not be given to starfire, so I did not look` });
    return;
  }
  const reason = lookReason(result.reason);
  const detail = plainDetail(result.error);
  await api.answer(request.request_id, { ok: false, reason, detail });
  show({ kind: 'failed', text: failureText(reason, app, detail) });
}

function isShowablePicture(picture) {
  return Boolean(picture) && LOOK_PICTURE_TYPES.has(picture.mime) && typeof picture.image === 'string' && LOOK_BASE64.test(picture.image);
}

function createLookView({ document, output, scrollToEnd }) {
  const lines = new Map();

  function lineFor(id) {
    if (lines.has(id)) return lines.get(id);
    const line = document.createElement('div');
    line.className = 'line info screen-look';
    const text = document.createElement('span');
    line.append(text);
    output.append(line);
    lines.set(id, line);
    return line;
  }

  function enlarge(source) {
    const overlay = document.createElement('div');
    overlay.className = 'look-overlay';
    const big = document.createElement('img');
    big.src = source;
    big.setAttribute('alt', 'Screenshot the model saw');
    overlay.append(big);
    overlay.addEventListener('click', () => overlay.remove());
    document.body.append(overlay);
  }

  function addThumbnail(line, picture) {
    const source = `data:${picture.mime};base64,${picture.image}`;
    const thumbnail = document.createElement('img');
    thumbnail.className = 'look-thumb';
    thumbnail.src = source;
    thumbnail.setAttribute('alt', 'Screenshot the model saw (click to enlarge)');
    thumbnail.addEventListener('click', () => enlarge(source));
    line.append(thumbnail);
  }

  function show(id, step) {
    const line = lineFor(id);
    line.children[0].textContent = step.text;
    line.className = step.kind === 'failed' ? 'line err screen-look' : 'line info screen-look';
    if (step.kind === 'done' && isShowablePicture(step.picture)) addThumbnail(line, step.picture);
    scrollToEnd();
  }

  return { show };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { LOOK_FAILURE_REASONS, stepText, failureText, runLook, desktopScreenConnected, createLookView };
}
