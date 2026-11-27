const { contextBridge, ipcRenderer } = require('electron');

const DESKTOP_VERSION = /^\d{1,6}(\.\d{1,6}){0,3}$/;
const desktopVersion = ipcRenderer.sendSync('starfire-web:version');

if (typeof desktopVersion === 'string' && DESKTOP_VERSION.test(desktopVersion)) {
  contextBridge.exposeInMainWorld('starfireDesktopApp', { version: desktopVersion });
}

const isOffered = ipcRenderer.sendSync('starfire-web:allowed') === true;
const isUnlinked = !isOffered && ipcRenderer.sendSync('starfire-web:unlinked') === true;
const STEP_VALUE_TYPES = new Set(['string', 'number', 'boolean']);

function plainStepValues(values) {
  if (values === null || typeof values !== 'object' || Array.isArray(values)) return {};
  const kept = {};
  for (const [name, value] of Object.entries(values)) {
    if (STEP_VALUE_TYPES.has(typeof value)) kept[name] = value;
  }
  return kept;
}

if (isOffered) {
  contextBridge.exposeInMainWorld('starfireDesktopLink', {
    linkStatus: () => ipcRenderer.invoke('starfire-web:status'),
    lookAtScreen: (app) => ipcRenderer.invoke('starfire-web:look', { app: typeof app === 'string' ? app : '' }),
    openInBrowser: (url, browser) => ipcRenderer.invoke('starfire-web:open', { url: typeof url === 'string' ? url : '', browser: typeof browser === 'string' ? browser : '' }),
    controlStep: (action, values) => ipcRenderer.invoke('starfire-web:control', { action: typeof action === 'string' ? action : '', arguments: plainStepValues(values) }),
  });
}

if (isUnlinked) {
  contextBridge.exposeInMainWorld('starfireDesktopUnlinked', true);
}

if (ipcRenderer.sendSync('starfire-web:reply-notice') === true) {
  contextBridge.exposeInMainWorld('starfireDesktopReplies', {
    ready: () => ipcRenderer.send('starfire-web:reply-ready'),
  });
}
