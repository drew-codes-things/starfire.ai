const { pathToFileURL } = require('node:url');

const WEB_PROTOCOLS = new Set(['http:', 'https:']);
const EXTERNAL_PROTOCOLS = new Set(['http:', 'https:', 'mailto:']);
const MEDIA_PERMISSION = 'media';
const MICROPHONE = 'audio';
const ALLOWED_PERMISSIONS = new Set([MEDIA_PERMISSION, 'notifications', 'clipboard-sanitized-write', 'fullscreen']);

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

function asksForMicrophoneOnly(details) {
  const source = details !== null && typeof details === 'object' ? details : {};
  let kinds = [];
  if (Array.isArray(source.mediaTypes)) kinds = source.mediaTypes;
  else if (typeof source.mediaType === 'string') kinds = [source.mediaType];
  return kinds.length > 0 && kinds.every((kind) => kind === MICROPHONE);
}

function isPermissionAllowed(permission, requestingUrl, origin, details) {
  if (!ALLOWED_PERMISSIONS.has(permission)) return false;
  const url = parseUrl(requestingUrl);
  if (url === null || url.origin !== origin) return false;
  return permission !== MEDIA_PERMISSION || asksForMicrophoneOnly(details);
}

function isLocalPageUrl(value, pagesDir) {
  const url = parseUrl(value);
  if (!url || url.protocol !== 'file:' || url.host !== '') return false;
  const base = pathToFileURL(pagesDir).pathname.replace(/\/?$/, '/');
  return url.pathname.startsWith(base) && !url.pathname.slice(base.length).includes('/');
}

function guardWebContents(contents, guard, { openExternal, loadInside, mayOpenExternal = () => false }) {
  function handleNavigation(details, url, isInPlace, isMainFrame) {
    const target = typeof details.url === 'string' ? details.url : url;
    const isTopFrame = typeof details.isMainFrame === 'boolean' ? details.isMainFrame : isMainFrame === true;
    const verdict = guard.decide(target);
    if (verdict === 'allow') return;
    details.preventDefault();
    if (verdict === 'external' && isTopFrame && mayOpenExternal()) openExternal(target);
  }
  contents.on('will-navigate', handleNavigation);
  contents.on('will-redirect', handleNavigation);
  contents.on('will-attach-webview', (event) => event.preventDefault());
  contents.setWindowOpenHandler(({ url }) => {
    const verdict = guard.decide(url);
    if (verdict === 'allow') loadInside(url);
    if (verdict === 'external' && mayOpenExternal()) openExternal(url);
    return { action: 'deny' };
  });
}

module.exports = { createNavigationGuard, isSafeExternalUrl, isPermissionAllowed, isLocalPageUrl, guardWebContents };
