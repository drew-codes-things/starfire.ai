const { contextBridge, ipcRenderer } = require('electron');

function call(channel, payload) {
  return ipcRenderer.invoke(channel, payload);
}

contextBridge.exposeInMainWorld('starfireDesktop', {
  getSettings: () => call('starfire:get-settings'),
  saveSettings: (patch) => call('starfire:save-settings', patch),
  connect: (address, allowInsecure) => call('starfire:connect', { address, allowInsecure }),
  describeKey: (event) => call('starfire:describe-key', {
    code: event.code,
    ctrlKey: event.ctrlKey,
    altKey: event.altKey,
    shiftKey: event.shiftKey,
    metaKey: event.metaKey,
  }),
  sendQuickAsk: (text) => call('starfire:quick-ask-send', text),
  hideQuickAsk: () => call('starfire:quick-ask-hide'),
  changeAddress: () => call('starfire:change-address'),
  retry: () => call('starfire:retry'),
  cancelConnect: () => call('starfire:cancel-connect'),
  onShown: (callback) => ipcRenderer.on('starfire:shown', () => callback()),
});
