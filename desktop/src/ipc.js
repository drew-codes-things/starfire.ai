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
};
const SHOWN_EVENT = 'starfire:shown';
const KEY_EVENT_FIELDS = ['code', 'ctrlKey', 'altKey', 'shiftKey', 'metaKey'];

function pickKeyEvent(raw) {
  const source = raw !== null && typeof raw === 'object' ? raw : {};
  const picked = { code: typeof source.code === 'string' ? source.code.slice(0, 32) : '' };
  for (const field of KEY_EVENT_FIELDS.slice(1)) picked[field] = source[field] === true;
  return picked;
}

function registerIpc({ ipcMain, controller, isLocalSender, actions, describeKey }) {
  function handle(channel, handler) {
    ipcMain.handle(channel, async (event, ...args) => {
      if (!isLocalSender(event)) throw new Error(`Refused ${channel}: it did not come from one of the app's own pages.`);
      return handler(...args);
    });
  }
  handle(CHANNELS.getSettings, () => ({ settings: controller.get(), shortcutProblem: actions.shortcutProblem() }));
  handle(CHANNELS.saveSettings, (patch) => controller.update(patch));
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
}

module.exports = { registerIpc, CHANNELS, SHOWN_EVENT, pickKeyEvent };
