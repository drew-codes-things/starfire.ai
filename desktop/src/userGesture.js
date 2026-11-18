const GESTURE_INPUT_TYPES = new Set(['mouseDown', 'rawKeyDown', 'keyDown', 'gestureTap', 'touchStart', 'pointerDown']);
const RECENT_GESTURE_MS = 10000;

function createGestureTracker({ now = Date.now, recentMs = RECENT_GESTURE_MS } = {}) {
  let lastGestureAt = null;

  function note(inputEvent) {
    if (inputEvent && GESTURE_INPUT_TYPES.has(inputEvent.type)) lastGestureAt = now();
  }

  function consume() {
    const isRecent = lastGestureAt !== null && now() - lastGestureAt <= recentMs;
    lastGestureAt = null;
    return isRecent;
  }

  function watch(contents) {
    contents.on('input-event', (event, inputEvent) => note(inputEvent));
  }

  return { note, consume, watch };
}

module.exports = { createGestureTracker, RECENT_GESTURE_MS };
