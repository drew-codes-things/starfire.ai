const { contextBridge, ipcRenderer } = require('electron');

const isOffered = ipcRenderer.sendSync('starfire-web:allowed') === true;

if (isOffered) {
  contextBridge.exposeInMainWorld('starfireDesktopLink', {
    linkStatus: () => ipcRenderer.invoke('starfire-web:status'),
    lookAtScreen: (app) => ipcRenderer.invoke('starfire-web:look', { app: typeof app === 'string' ? app : '' }),
  });
}
