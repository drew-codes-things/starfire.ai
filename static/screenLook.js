'use strict';

const SCREEN_LOOK_URL = '/api/screen-looks/';
let screenLookView = null;

function lookView() {
  if (!screenLookView) {
    screenLookView = createLookView({ document, output: els.output, scrollToEnd: () => { els.output.scrollTop = els.output.scrollHeight; } });
  }
  return screenLookView;
}

async function claimScreenLook(requestId) {
  try {
    const response = await fetch(`${SCREEN_LOOK_URL}${encodeURIComponent(requestId)}/claim`, { method: 'POST' });
    return response.ok ? await response.json() : null;
  } catch (error) {
    return null;
  }
}

async function answerScreenLook(requestId, outcome) {
  try {
    const response = await fetch(`${SCREEN_LOOK_URL}${encodeURIComponent(requestId)}/result`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(outcome),
    });
    return response.ok;
  } catch (error) {
    return false;
  }
}

function startScreenLook(request) {
  runLook({
    request,
    link: window.starfireDesktopLink,
    api: { claim: claimScreenLook, answer: answerScreenLook },
    show: step => lookView().show(request.request_id, step),
  }).catch(error => printLine('error: ' + error.message, 'err'));
}

function desktopScreenOffered() {
  return desktopScreenConnected(window.starfireDesktopLink);
}
