'use strict';

function tellDesktopTheReplyIsReady(win) {
  const replies = win ? win.starfireDesktopReplies : null;
  if (replies && typeof replies.ready === 'function') replies.ready();
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { tellDesktopTheReplyIsReady };
}
