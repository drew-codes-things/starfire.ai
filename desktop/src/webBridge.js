const WEB_CHANNELS = {
  allowed: 'starfire-web:allowed',
  status: 'starfire-web:status',
  look: 'starfire-web:look',
  open: 'starfire-web:open',
};
const LOOK_FIELDS = ['app'];
const OPEN_FIELDS = ['url', 'browser'];

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

function isTextRequest(request, fields) {
  if (request === null || typeof request !== 'object' || Array.isArray(request)) return false;
  const keys = Object.keys(request);
  return keys.length === fields.length && fields.every((field) => keys.includes(field) && typeof request[field] === 'string');
}

function isLookRequest(request) {
  return isTextRequest(request, LOOK_FIELDS);
}

function isOpenRequest(request) {
  return isTextRequest(request, OPEN_FIELDS);
}

function pageStatus(status) {
  const connected = status.state === 'connected';
  return { connected, capabilities: connected && Array.isArray(status.capabilities) ? status.capabilities : [] };
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
  handle(WEB_CHANNELS.status, async () => pageStatus(await artyLink.status()));
  handle(WEB_CHANNELS.look, async (request) => {
    if (!isLookRequest(request)) return refuseLook('A look takes only the name of an app.');
    return artyLink.lookAtScreen(request.app);
  });
  handle(WEB_CHANNELS.open, async (request) => {
    if (!isOpenRequest(request)) return refuseLook('An open takes only a web address and a browser name.');
    return artyLink.openInBrowser(request.url, request.browser);
  });
}

module.exports = { WEB_CHANNELS, isBridgeOffered, isStarfirePage, registerWebBridge };
