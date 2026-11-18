const WEB_CHANNELS = {
  allowed: 'starfire-web:allowed',
  status: 'starfire-web:status',
  look: 'starfire-web:look',
};
const LOOK_FIELDS = ['app'];

function isBridgeOffered(settings) {
  return settings.artyLink === true && settings.artyToken !== '';
}

function originOf(value) {
  try {
    return new URL(value).origin;
  } catch (error) {
    return '';
  }
}

function isStarfirePage(frame, address) {
  if (!frame || frame.parent || typeof frame.url !== 'string' || address === '') return false;
  const origin = originOf(frame.url);
  return origin !== '' && origin !== 'null' && origin === originOf(address);
}

function refuseLook(error) {
  return { ok: false, reason: 'bad_request', error };
}

function isLookRequest(request) {
  if (request === null || typeof request !== 'object' || Array.isArray(request)) return false;
  const keys = Object.keys(request);
  return keys.length === LOOK_FIELDS.length && keys.every((key, index) => key === LOOK_FIELDS[index]) && typeof request.app === 'string';
}

function registerWebBridge({ ipcMain, controller, artyLink, isMainWindow }) {
  function maySpeak(event) {
    const settings = controller.get();
    return isBridgeOffered(settings) && isMainWindow(event) && isStarfirePage(event.senderFrame, settings.address);
  }

  function handle(channel, handler) {
    ipcMain.handle(channel, async (event, ...args) => {
      if (!maySpeak(event)) throw new Error(`Refused ${channel}: it did not come from the starfire page with the Arty Desktop link on.`);
      return handler(...args);
    });
  }

  ipcMain.on(WEB_CHANNELS.allowed, (event) => {
    event.returnValue = maySpeak(event);
  });
  handle(WEB_CHANNELS.status, async () => ({ connected: (await artyLink.status()).state === 'connected' }));
  handle(WEB_CHANNELS.look, async (request) => {
    if (!isLookRequest(request)) return refuseLook('A look takes only the name of an app.');
    return artyLink.lookAtScreen(request.app);
  });
}

module.exports = { WEB_CHANNELS, isBridgeOffered, isStarfirePage, registerWebBridge };
