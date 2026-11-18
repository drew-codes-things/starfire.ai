const STARFIRE_PARTITION = 'persist:starfire';
const LOCAL_PARTITION = 'local-pages';

function secureWebPreferences(extra = {}) {
  return {
    ...extra,
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInSubFrames: false,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    webviewTag: false,
    experimentalFeatures: false,
  };
}

module.exports = { secureWebPreferences, STARFIRE_PARTITION, LOCAL_PARTITION };
