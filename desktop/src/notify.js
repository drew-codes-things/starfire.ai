const CHAT_STREAM_PATH = '/api/chat_stream';
const OK_STATUS = 200;

function isReplyFinished(details, origin) {
  if (!details || details.method !== 'POST' || details.statusCode !== OK_STATUS) return false;
  let url;
  try {
    url = new URL(details.url);
  } catch (error) {
    return false;
  }
  return url.origin === origin && url.pathname === CHAT_STREAM_PATH;
}

function shouldNotify({ enabled, windowVisible, windowFocused }) {
  if (!enabled) return false;
  return !(windowVisible && windowFocused);
}

function replyNotification() {
  return { title: 'starfire', body: 'Your reply is ready.', silent: false };
}

module.exports = { isReplyFinished, shouldNotify, replyNotification, CHAT_STREAM_PATH };
