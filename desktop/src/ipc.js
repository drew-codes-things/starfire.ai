const { publicSettings } = require('./settings');

const CHANNELS = {
  getSettings: 'starfire:get-settings',
  saveSettings: 'starfire:save-settings',
  connect: 'starfire:connect',
  describeKey: 'starfire:describe-key',
  quickAskSend: 'starfire:quick-ask-send',
  quickAskHide: 'starfire:quick-ask-hide',
  changeAddress: 'starfire:change-address',
  retry: 'starfire:retry',
  cancelConnect: 'starfire:cancel-connect',
  linkStatus: 'starfire:link-status',
  linkPair: 'starfire:link-pair',
  linkUnpair: 'starfire:link-unpair',
  linkTry: 'starfire:link-try',
};
const SHOWN_EVENT = 'starfire:shown';
const KEY_EVENT_FIELDS = ['code', 'ctrlKey', 'altKey', 'shiftKey', 'metaKey'];

function pickKeyEvent(raw) {
  const source = raw !== null && typeof raw === 'object' ? raw : {};
  const picked = { code: typeof source.code === 'string' ? source.code.slice(0, 32) : '' };
  for (const field of KEY_EVENT_FIELDS.slice(1)) picked[field] = source[field] === true;
  return picked;
}

function asObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function registerIpc({ ipcMain, controller, isLocalSender, isSettingsSender, actions, describeKey }) {
  function handle(channel, handler) {
    ipcMain.handle(channel, async (event, ...args) => {
      if (!isLocalSender(event)) throw new Error(`Refused ${channel}: it did not come from one of the app's own pages.`);
      return handler(...args);
    });
  }
  function handleFromSettings(channel, handler) {
    ipcMain.handle(channel, async (event, ...args) => {
      if (!isSettingsSender(event)) throw new Error(`Refused ${channel}: only the settings page uses the Arty Desktop link.`);
      return handler(event, ...args);
    });
  }
  handle(CHANNELS.getSettings, () => ({ settings: publicSettings(controller.get()), shortcutProblem: actions.shortcutProblem() }));
  handle(CHANNELS.saveSettings, (patch) => {
    const result = controller.update(patch);
    return result.ok ? { ...result, settings: publicSettings(result.settings) } : result;
  });
  handle(CHANNELS.connect, async (request) => {
    const body = request !== null && typeof request === 'object' ? request : {};
    const result = await controller.connect({ address: body.address, allowInsecure: body.allowInsecure === true });
    if (result.ok) actions.connected();
    return result;
  });
  handle(CHANNELS.describeKey, (event) => describeKey(pickKeyEvent(event)));
  handle(CHANNELS.quickAskSend, (text) => actions.submitQuickAsk(text));
  handle(CHANNELS.quickAskHide, () => actions.hideQuickAsk());
  handle(CHANNELS.changeAddress, () => actions.openConnect('change'));
  handle(CHANNELS.retry, () => actions.retry());
  handle(CHANNELS.cancelConnect, () => actions.closeConnect());
  handleFromSettings(CHANNELS.linkStatus, () => actions.linkStatus());
  handleFromSettings(CHANNELS.linkPair, (event, request) => actions.linkPair(asObject(request).code));
  handleFromSettings(CHANNELS.linkUnpair, () => actions.linkUnpair());
  handleFromSettings(CHANNELS.linkTry, (event) => actions.linkTry(event.sender));
}

module.exports = { registerIpc, CHANNELS, SHOWN_EVENT, pickKeyEvent };
