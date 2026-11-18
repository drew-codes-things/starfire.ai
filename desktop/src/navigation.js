const { pathToFileURL } = require('node:url');

const WEB_PROTOCOLS = new Set(['http:', 'https:']);
const EXTERNAL_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);
const ALLOWED_PERMISSIONS = new Set(['media', 'notifications', 'clipboard-sanitized-write', 'fullscreen']);

function parseUrl(value) {
  if (typeof value !== 'string' || value === '') return null;
  try {
    return new URL(value);
  } catch (error) {
    return null;
  }
}

function createNavigationGuard(origin) {
  function decide(value) {
    const url = parseUrl(value);
    if (!url) return 'deny';
    if (url.protocol === 'mailto:') return 'external';
    if (!WEB_PROTOCOLS.has(url.protocol)) return 'deny';
    return url.origin === origin ? 'allow' : 'external';
  }
  return { origin, decide };
}

function isSafeExternalUrl(value) {
  const url = parseUrl(value);
  return url !== null && EXTERNAL_PROTOCOLS.has(url.protocol);
}

function isPermissionAllowed(permission, requestingUrl, origin) {
  if (!ALLOWED_PERMISSIONS.has(permission)) return false;
  const url = parseUrl(requestingUrl);
  return url !== null && url.origin === origin;
}

function isLocalPageUrl(value, pagesDir) {
  const url = parseUrl(value);
  if (!url || url.protocol !== 'file:') return false;
  const base = pathToFileURL(pagesDir).pathname.replace(/\/?$/, '/');
  return url.pathname.startsWith(base) && !url.pathname.slice(base.length).includes('/');
}

function guardWebContents(contents, guard, { openExternal, loadInside }) {
  function handleNavigation(event, url) {
    const verdict = guard.decide(url);
    if (verdict === 'allow') return;
    event.preventDefault();
    if (verdict === 'external') openExternal(url);
  }
  contents.on('will-navigate', handleNavigation);
  contents.on('will-redirect', handleNavigation);
  contents.on('will-attach-webview', (event) => event.preventDefault());
  contents.setWindowOpenHandler(({ url }) => {
    const verdict = guard.decide(url);
    if (verdict === 'allow') loadInside(url);
    if (verdict === 'external') openExternal(url);
    return { action: 'deny' };
  });
}

module.exports = { createNavigationGuard, isSafeExternalUrl, isPermissionAllowed, isLocalPageUrl, guardWebContents };
