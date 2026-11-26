'use strict';

const DESKTOP_UPDATE_FIRST_CHECK_MS = 5000;
const DESKTOP_UPDATE_EVERY_MS = 24 * 60 * 60 * 1000;
const DESKTOP_UPDATE_DISMISSED_KEY = 'sf_desktop_update_dismissed';
const DESKTOP_RELEASE_PAGE = 'https://github.com/drew-codes-things/starfire.ai/releases/tag/desktop-v';
const DESKTOP_VERSION_TEXT = /^(?:desktop-)?v?(\d{1,6}(?:\.\d{1,6}){0,3})$/;

function desktopVersionParts(text) {
  if (typeof text !== 'string') return null;
  const match = DESKTOP_VERSION_TEXT.exec(text);
  return match ? match[1].split('.').map(Number) : null;
}

function isNewerDesktopVersion(latest, current) {
  const latestParts = desktopVersionParts(latest);
  const currentParts = desktopVersionParts(current);
  if (!latestParts || !currentParts) return false;
  for (let index = 0; index < Math.max(latestParts.length, currentParts.length); index += 1) {
    const difference = (latestParts[index] || 0) - (currentParts[index] || 0);
    if (difference !== 0) return difference > 0;
  }
  return false;
}

function desktopAppVersion(win) {
  const desktopApp = win ? win.starfireDesktopApp : null;
  const version = desktopApp ? desktopApp.version : null;
  return desktopVersionParts(version) ? version : '';
}

function isDesktopReleasePage(url, version) {
  return typeof url === 'string' && url === DESKTOP_RELEASE_PAGE + version;
}

function desktopUpdateToShow(answer, currentVersion, dismissedVersion) {
  if (!answer || answer.check !== true || !isNewerDesktopVersion(answer.version, currentVersion)) return null;
  if (!isDesktopReleasePage(answer.url, answer.version) || answer.version === dismissedVersion) return null;
  return { version: answer.version, url: answer.url };
}

function readDismissedVersion(storage) {
  try {
    return storage.getItem(DESKTOP_UPDATE_DISMISSED_KEY) || '';
  } catch (_) {
    return '';
  }
}

function saveDismissedVersion(storage, version) {
  try {
    storage.setItem(DESKTOP_UPDATE_DISMISSED_KEY, version);
  } catch (_) {
    return false;
  }
  return true;
}

function createDesktopUpdateNotice({ document, writeDismissed }) {
  let notice = null;
  let shownVersion = '';

  function build() {
    const box = document.getElementById('toasts');
    if (!box) return null;
    const element = document.createElement('div');
    element.className = 'toast desktop-update';
    element.setAttribute('role', 'status');
    const text = document.createElement('span');
    const link = document.createElement('a');
    link.setAttribute('target', '_blank');
    link.setAttribute('rel', 'noopener noreferrer');
    link.textContent = 'Open the release page';
    const dismiss = document.createElement('button');
    dismiss.setAttribute('type', 'button');
    dismiss.className = 'desktop-update-dismiss';
    dismiss.textContent = 'Dismiss';
    dismiss.addEventListener('click', () => {
      writeDismissed(shownVersion);
      hide();
    });
    element.append(text, link, dismiss);
    box.appendChild(element);
    return { element, text, link };
  }

  function hide() {
    if (notice) notice.element.remove();
    notice = null;
  }

  function show(update) {
    if (!notice) notice = build();
    if (!notice) return;
    shownVersion = update.version;
    notice.text.textContent = `Version ${update.version} is available`;
    notice.link.setAttribute('href', update.url);
  }

  return { show };
}

async function checkDesktopUpdate({ fetchLatest, currentVersion, storage, notice, log }) {
  try {
    const update = desktopUpdateToShow(await fetchLatest(), currentVersion, readDismissedVersion(storage));
    if (update) notice.show(update);
    return update;
  } catch (error) {
    log(`Could not check for a new starfire Desktop: ${error && error.message ? error.message : error}`);
    return null;
  }
}

function startDesktopUpdateCheck({ win, document, fetchLatest, storage, timers, log }) {
  const currentVersion = desktopAppVersion(win);
  if (!currentVersion) return false;
  const notice = createDesktopUpdateNotice({ document, writeDismissed: (version) => saveDismissedVersion(storage, version) });
  const check = () => checkDesktopUpdate({ fetchLatest, currentVersion, storage, notice, log });
  timers.setTimeout(check, DESKTOP_UPDATE_FIRST_CHECK_MS);
  timers.setInterval(check, DESKTOP_UPDATE_EVERY_MS);
  return true;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    DESKTOP_UPDATE_FIRST_CHECK_MS, DESKTOP_UPDATE_EVERY_MS, DESKTOP_UPDATE_DISMISSED_KEY,
    isNewerDesktopVersion, desktopAppVersion, desktopUpdateToShow, createDesktopUpdateNotice, startDesktopUpdateCheck,
  };
}
