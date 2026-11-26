'use strict';

const CONTROL_STEP_URL = '/api/control-steps/';
let desktopControlView = null;

function controlView() {
  if (!desktopControlView) {
    desktopControlView = createControlView({ document, output: els.output, scrollToEnd: () => { els.output.scrollTop = els.output.scrollHeight; } });
  }
  return desktopControlView;
}

async function claimControlStep(requestId) {
  try {
    const response = await fetch(`${CONTROL_STEP_URL}${encodeURIComponent(requestId)}/claim`, { method: 'POST' });
    return response.ok ? await response.json() : null;
  } catch (error) {
    return null;
  }
}

async function answerControlStep(requestId, outcome) {
  try {
    const response = await fetch(`${CONTROL_STEP_URL}${encodeURIComponent(requestId)}/result`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(outcome),
    });
    return response.ok;
  } catch (error) {
    return false;
  }
}

function startControlStep(request) {
  runControlStep({
    request,
    link: window.starfireDesktopLink,
    api: { claim: claimControlStep, answer: answerControlStep },
    show: step => controlView().show(request.request_id, step),
  }).catch(error => printLine('error: ' + error.message, 'err'));
}

function desktopControlForThisChat() {
  return desktopControlState(window.starfireDesktopLink, window.starfireDesktopUnlinked === true);
}
