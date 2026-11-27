function shouldNotify({ enabled, windowVisible, windowFocused }) {
  if (!enabled) return false;
  return !(windowVisible && windowFocused);
}

function replyNotification() {
  return { title: 'starfire', body: 'Your reply is ready.', silent: false };
}

module.exports = { shouldNotify, replyNotification };
