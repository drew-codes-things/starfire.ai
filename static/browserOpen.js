'use strict';

const BROWSER_OPEN_URL = '/api/browser-opens/';
let browserOpenView = null;

function openView() {
  if (!browserOpenView) {
    browserOpenView = createOpenView({ document, output: els.output, scrollToEnd: () => { els.output.scrollTop = els.output.scrollHeight; } });
  }
  return browserOpenView;
}

async function claimBrowserOpen(requestId) {
  try {
    const response = await fetch(`${BROWSER_OPEN_URL}${encodeURIComponent(requestId)}/claim`, { method: 'POST' });
    return response.ok ? await response.json() : null;
  } catch (error) {
    return null;
  }
}

async function answerBrowserOpen(requestId, outcome) {
  try {
    const response = await fetch(`${BROWSER_OPEN_URL}${encodeURIComponent(requestId)}/result`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(outcome),
    });
    return response.ok;
  } catch (error) {
    return false;
  }
}

function startBrowserOpen(request) {
  runBrowserOpen({
    request,
    link: window.starfireDesktopLink,
    api: { claim: claimBrowserOpen, answer: answerBrowserOpen },
    show: step => openView().show(request.request_id, step),
  }).catch(error => printLine('error: ' + error.message, 'err'));
}

function desktopBrowserOffered() {
  return desktopBrowserAvailable(window.starfireDesktopLink);
}
