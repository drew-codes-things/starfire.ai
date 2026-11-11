'use strict';

const HOME_PATH = '/';
const NOT_ALLOWED_IN_NEXT = /[\u0000-\u001f\u007f\\]/;

function safeNextPath(search) {
  let wanted = '';
  try {
    wanted = new URLSearchParams(search || '').get('next') || '';
  } catch {
    return HOME_PATH;
  }
  if (!wanted.startsWith('/') || wanted.startsWith('//') || NOT_ALLOWED_IN_NEXT.test(wanted)) return HOME_PATH;
  return wanted;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { safeNextPath };
}
