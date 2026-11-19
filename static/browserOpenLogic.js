'use strict';

const OPEN_FAILURE_REASONS = new Set(['declined', 'timed_out', 'abandoned', 'busy', 'bad_request', 'no_browser', 'which_browser', 'not_connected', 'unavailable', 'not_running', 'not_linked', 'failed']);
const OPEN_MAX_DETAIL_CHARS = 300;
const OPEN_MAX_BROWSER_CHARS = 40;
const OPEN_ANY_BROWSER = 'your browser';
const OPEN_CAPABILITY = 'browser.open';

function openBrowserLabel(browser) {
  return browser || OPEN_ANY_BROWSER;
}

function openStepText(kind, url, browser) {
  if (kind === 'opening') return `Opening ${url} in ${openBrowserLabel(browser)} on your PC...`;
  if (kind === 'waiting') return 'Waiting for you to allow it in Arty';
  return `Opened ${url} in ${openBrowserLabel(browser)}`;
}

function openFailureText(reason, url, browser, detail) {
  if (reason === 'declined') return `You said no in Arty, so I did not open ${url}`;
  if (reason === 'timed_out') return `Nobody allowed it in Arty in time, so I did not open ${url}`;
  if (reason === 'not_running') return `Arty Desktop is not running, so I could not open ${url}`;
  if (reason === 'not_linked') return `starfire Desktop is not linked to Arty Desktop, so I could not open ${url}`;
  if (reason === 'no_browser') return `No browser is connected to Arty, so I could not open ${url}`;
  return `Could not open ${url} in ${openBrowserLabel(browser)}: ${detail || 'it failed'}`;
}

function openReason(reason) {
  return OPEN_FAILURE_REASONS.has(reason) ? reason : 'failed';
}

async function desktopBrowserStatus(link) {
  if (!link || typeof link.linkStatus !== 'function') return { connected: false, available: false };
  try {
    const status = await link.linkStatus();
    const capabilities = Array.isArray(status.capabilities) ? status.capabilities : [];
    const available = capabilities.some((capability) => capability && capability.id === OPEN_CAPABILITY && capability.available === true);
    return { connected: status.connected === true, available: status.connected === true && available };
  } catch (error) {
    return { connected: false, available: false };
  }
}

async function desktopBrowserAvailable(link) {
  return (await desktopBrowserStatus(link)).available;
}

async function unreadyOpenFailure(link) {
  if (!link) return { ok: false, reason: 'not_linked', error: '' };
  const status = await desktopBrowserStatus(link);
  if (!status.connected) return { ok: false, reason: 'not_running', error: '' };
  if (!status.available) return { ok: false, reason: 'unavailable', error: 'Opening pages is not available in Arty Desktop.' };
  return null;
}

async function openThroughArty(link, url, browser, show) {
  const unready = await unreadyOpenFailure(link);
  if (unready) return unready;
  show({ kind: 'waiting', text: openStepText('waiting', url, browser) });
  try {
    return await link.openInBrowser(url, browser);
  } catch (error) {
    return { ok: false, reason: 'failed', error: 'starfire Desktop could not reach Arty Desktop.' };
  }
}

function isOpenClaim(claimed) {
  return Boolean(claimed) && typeof claimed === 'object' && typeof claimed.url === 'string' && claimed.url !== '' && typeof claimed.browser === 'string';
}

function openedAnswer(result) {
  const browser = typeof result.browser === 'string' ? result.browser.slice(0, OPEN_MAX_BROWSER_CHARS) : '';
  return { ok: true, browser, tab: Number.isSafeInteger(result.tab) && result.tab >= 0 ? result.tab : null };
}

function openDetail(value) {
  return typeof value === 'string' ? value.slice(0, OPEN_MAX_DETAIL_CHARS) : '';
}

async function runBrowserOpen({ request, link, api, show }) {
  const claimed = await api.claim(request.request_id);
  if (!isOpenClaim(claimed)) return;
  const { url, browser } = claimed;
  show({ kind: 'opening', text: openStepText('opening', url, browser) });
  const result = await openThroughArty(link, url, browser, show);
  if (result.ok === true) {
    const answer = openedAnswer(result);
    if (await api.answer(request.request_id, answer)) {
      show({ kind: 'done', text: openStepText('done', url, answer.browser || browser) });
      return;
    }
    show({ kind: 'failed', text: `Arty may have opened ${url}, but I could not tell starfire, so the reply will not know` });
    return;
  }
  const reason = openReason(result.reason);
  const detail = openDetail(result.error);
  await api.answer(request.request_id, { ok: false, reason, detail });
  show({ kind: 'failed', text: openFailureText(reason, url, browser, detail) });
}

function createOpenView({ document, output, scrollToEnd }) {
  const lines = new Map();

  function lineFor(id) {
    if (lines.has(id)) return lines.get(id);
    const line = document.createElement('div');
    line.className = 'line info browser-open';
    const text = document.createElement('span');
    line.append(text);
    output.append(line);
    lines.set(id, line);
    return line;
  }

  function show(id, step) {
    const line = lineFor(id);
    line.children[0].textContent = step.text;
    line.className = step.kind === 'failed' ? 'line err browser-open' : 'line info browser-open';
    scrollToEnd();
  }

  return { show };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { OPEN_FAILURE_REASONS, openStepText, openFailureText, runBrowserOpen, desktopBrowserAvailable, createOpenView };
}
